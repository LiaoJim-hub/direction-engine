# -*- coding: utf-8 -*-
"""标定产物组装与自校验的测试（2026-10-01）。"""
import datetime

import pytest

from direction_drift.calibration.record import (
    build_calibration_record,
    required_formal_fields,
    validate_calibration_record,
)

CALIB = {"auc": 0.977, "suggested_low": 0.125, "suggested_high": 0.132,
         "target_precision": 0.9, "achieved_precision": 0.8, "achieved_recall": 0.8,
         "n_samples": 298,
         "inputs": {"encoder": "BAAI/bge-small-zh-v1.5", "weights": None,
                    "prompt_version": "4.0"}}


def _ok(**over):
    base = dict(calibration=CALIB, mode="formal", source="298 条真实档案",
                n=298, n_positive=15, encoder="BAAI/bge-small-zh-v1.5",
                weights={"cone_alignment": 0.4}, prompt_version="4.0",
                card_fingerprint="c2:55be644d4c446514")
    base.update(over)
    return build_calibration_record(**base)


def test_builds_a_valid_formal_record():
    r = _ok()
    assert r["mode"] == "formal"
    assert r["low"] == 0.125 and r["high"] == 0.132
    assert r["n"] == 298 and r["n_positive"] == 15
    validate_calibration_record(r)                       # 不抛即通过


def test_manual_operating_point_overrides_suggestion():
    """操作点是业务权衡（漏报代价 >> 误报代价 → 偏查全），
    正式标定通常要人工拍板，故必须能被显式覆盖。"""
    r = _ok(low=0.122, high=0.126)
    assert r["low"] == 0.122 and r["high"] == 0.126


@pytest.mark.parametrize("bad_mode", ["", "Formal", "正式", None, "强标定"])
def test_mode_must_be_one_of_the_three_values(bad_mode):
    """认识论状态不是自由文本——写错会让消费端无法三值判定。"""
    with pytest.raises(ValueError, match="mode"):
        _ok(mode=bad_mode)


@pytest.mark.parametrize("field", ["encoder", "weights", "prompt_version",
                                   "card_fingerprint"])
def test_formal_calibration_requires_self_description(field):
    """正式标定必须自描述。这四样一旦缺，别人就无法套用你的操作点。"""
    with pytest.raises(ValueError, match="自描述"):
        _ok(**{field: None})


def _hand_written(**over):
    """手写一份正式标定记录——**守卫真正的对象**。

    产出脚本永远会写上 `card_fingerprint_status`（它由"有没有指纹"推出），
    所以"这个字段缺了 / 写错了"只可能发生在**手写**记录里。守卫要拦的是
    手写，就得直测 `validate_calibration_record`，而不是绕产出脚本。
    """
    rec = {"mode": "formal", "low": 0.125, "high": 0.132, "auc": 0.977,
           "n": 298, "n_positive": 15, "source": "手写",
           "encoder": "BAAI/bge-small-zh-v1.5",
           "weights": {"cone_alignment": 0.4}, "prompt_version": "4.0",
           "card_fingerprint": "c2:55be644d4c446514",
           "card_fingerprint_status": "backfilled"}
    rec.update(over)
    return rec


def test_hand_written_record_without_fingerprint_status_is_rejected():
    """「没写这个字段」与「写了 missing」不是一回事：前者是不知道，
    后者是知道没有。允许前者等于允许把"不知道"当成"没有"。"""
    rec = _hand_written()
    del rec["card_fingerprint_status"]
    with pytest.raises(ValueError, match="自描述"):
        validate_calibration_record(rec)


@pytest.mark.parametrize("bad_status", [
    "recorded_at_calibraton",        # 少一个 i —— 最现实的一种错
    "RECORDED_AT_CALIBRATION",       # 大小写
    "recorded at calibration",       # 空格
    "标定当场记录",                    # 换成中文
    "backfill",                      # 少 ed
    "",                              # 空串（比缺字段更隐蔽：键在，值是空）
])
def test_fingerprint_status_must_be_a_closed_set(bad_status):
    """`card_fingerprint_status` 不是自由文本。

    它已经被三个消费端按**字面值**分支：`calib_meta.py`、
    `card_page.py`、`inspect_cycle.py`。写错一个字母不会报错，
    只会三处**同时**掉进 else，把「标定当场记录」读成「没有来源信息」——
    静默降级，且没有任何一处会喊。唯一来得及拦的地方是产出口。
    """
    with pytest.raises(ValueError, match="card_fingerprint_status"):
        validate_calibration_record(_hand_written(card_fingerprint_status=bad_status))


def test_fingerprint_status_cannot_be_set_by_the_caller():
    """状态由「有没有指纹」**推出**，不接受调用方指定。

    否则脚本可以自报 `recorded_at_calibration`——而这恰恰是它唯一能提供的
    证据本身（"这个指纹是标定当场写下的"）。让调用方能设，就等于让被测者
    自己写鉴定结论。
    """
    with pytest.raises(ValueError, match="重名"):
        _ok(card_fingerprint_status="recorded_at_calibration")


def test_closed_set_covers_every_value_the_consumers_branch_on():
    """闭集必须**恰好**覆盖消费端实际分支的三个值，不多不少。

    这条断言把「生产端」与「消费端」的隐含契约摆到明面上：一旦有消费端
    新增第四个状态，这里先红，而不是等到线上把新状态读成旧状态。
    """
    from direction_drift.calibration.record import CARD_FINGERPRINT_STATUSES

    assert set(CARD_FINGERPRINT_STATUSES) == {
        "recorded_at_calibration",   # card_page.py: 「标定当场写下」
        "backfilled",                # card_page.py / calib_meta.py / inspect_cycle.py
        "missing",                   # 记录里根本没有指纹
    }
    assert len(CARD_FINGERPRINT_STATUSES) == 3


def test_card_fingerprint_status_says_recorded_not_backfilled():
    """**本模块存在的直接产物。**

    由脚本写下的指纹，其状态是「标定当场记录」（recorded_at_calibration），
    而不是事后补记（backfilled）。这两者的差别不是措辞：
    backfilled 只说明"自补记以来本卡未变"，**不说明标定当时用的就是这张卡**。
    这正是「加一个产出脚本」相对于「给标定函数加第四个参数」的全部差别所在。
    """
    r = _ok()
    assert r["card_fingerprint_status"] == "recorded_at_calibration"
    assert r["card_fingerprint_status"] != "backfilled"


def test_synthetic_mode_does_not_require_self_description():
    """兜底路径不要求自描述，但 mode 要如实写（三值里的一值，不是隐身）。"""
    r = build_calibration_record(calibration=CALIB, mode="synthetic",
                                 source="无正式标定时的兜底", n=48, n_positive=8)
    assert r["mode"] == "synthetic"
    assert r["card_fingerprint_status"] == "missing"
    validate_calibration_record(r)


def test_reporting_fields_cannot_clobber_fixed_keys():
    """audit / label_provenance 这类报告字段逐份不同，故不固定签名；
    但**不许覆盖已算出的口径**——那等于在无声中改掉标定的含义。"""
    with pytest.raises(ValueError, match="重名"):
        _ok(auc=0.001)          # 试图用 reporting 覆盖真实的 auc


def test_reporting_fields_are_kept():
    r = _ok(label_provenance="15 条正类逐组确认", recall_at_low="12/15",
            audit=[{"i": 1, "label": 0}])
    assert r["label_provenance"] == "15 条正类逐组确认"
    assert r["recall_at_low"] == "12/15"
    assert r["audit"] == [{"i": 1, "label": 0}]


def test_zero_valued_low_is_not_read_as_missing():
    """真值判断的坑：`low = 0.0` 是合法值，用 `not record[k]` 会把它误报成缺失。
    这类"用真值判断代替在场判断"的 bug，在阈值边上最容易发生。"""
    r = _ok(low=0.0)
    assert r["low"] == 0.0
    validate_calibration_record(r)                        # 不得因 0.0 而报缺失


def test_created_defaults_to_today():
    assert _ok()["created"] == datetime.date.today().isoformat()


def test_calibration_inputs_are_carried_through():
    """roc.py 的回显层带进来，作为"这份标定在什么环境下算的"的交叉印证。"""
    assert _ok()["calibration_inputs"]["encoder"] == "BAAI/bge-small-zh-v1.5"


def test_required_fields_list_is_exposed():
    """字段清单要能被枚举——文档与守卫都该用它，而不是各自手抄一份。"""
    f = required_formal_fields()
    for k in ("card_fingerprint", "encoder", "weights", "prompt_version",
              "mode", "low", "high", "auc"):
        assert k in f, f"清单里缺 {k}"

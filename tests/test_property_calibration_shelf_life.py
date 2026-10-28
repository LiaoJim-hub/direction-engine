# -*- coding: utf-8 -*-
"""性质 ⑤ 的可验证性：标定有保质期（2026-10-28）。

**门面原话（README 逐字）**：

> 「**标定有保质期**：标定文件绑定提示词版本（`prompt_version`），智能体改版后
> **引擎主动提醒复检**——阈值不是永久的。」

**2026-10-28 的盘点发现了一个比"缺测试"更坏的事**：

`cli.py:581` 的 `--prompt-version` 帮助文本写着
「标定保质期锚点：改提示词后同步改它，**否则标定会静默过期**」，
而**全包里一个字节的检查都没有** —— `stale` / `expire` / `recheck` /
`needs_review` / `该复检` 各 0 次，`verify.py` 里 `prompt_version` 0 次。
`prompt_version` 只被**写进**产物（6 处），**从来没有被读出来比对过**。

⇒ 故「引擎主动提醒复检」在 2026-10-28 之前是**一个 null 指针**：
docstring 承诺的行为，代码里不存在。

本文件测的是 `calibration/record.py` 里新加的那一格
（`check_prompt_version` / `prompt_version_warning`）。

**为什么是三值而不是布尔**：`prompt_version` 是 2026-10 之后才进 `REQUIRED_FORMAL`
的，**此前产出的标定里它可能是 None**。把它当 `stale` 会把一批本来有效的旧标定
判死；把它当 `match` 则是**把不知道渲染成通过**——那是本仓 §10 第 6 条说的伪造。
"""
import pytest

from direction_drift.calibration.record import (
    PROMPT_VERSION_STATUSES,
    check_prompt_version,
    prompt_version_warning,
)


# --------------------------------------------------------------------------
# 正测：stale 必须被判出来
# --------------------------------------------------------------------------
def test_stale_when_prompt_version_changed():
    """★ 核心：标定在 1.0，而当前是 1.2 ⇒ 必须 stale。"""
    rec = {"prompt_version": "1.0"}
    r = check_prompt_version(rec, "1.2")
    assert r["status"] == "stale"
    assert r["recorded"] == "1.0"
    assert r["current"] == "1.2"
    assert "1.0" in r["reason"] and "1.2" in r["reason"]


def test_match_when_same_prompt_version():
    assert check_prompt_version({"prompt_version": "2.7"}, "2.7")["status"] == "match"


def test_match_ignores_surrounding_whitespace():
    """★ 版本号是手填的，前后空格不该造出假的 stale。"""
    assert check_prompt_version({"prompt_version": " 1.0 "}, "1.0")["status"] == "match"
    assert check_prompt_version({"prompt_version": "1.0"}, " 1.0 ")["status"] == "match"


# --------------------------------------------------------------------------
# 反测：unknown 不能被渲染成 stale 或 match
# --------------------------------------------------------------------------
def test_unknown_when_record_has_no_prompt_version():
    """★ 旧产物没记版本 ⇒ unknown（★ 不得判绿，也不得判红）。"""
    r = check_prompt_version({}, "1.2")
    assert r["status"] == "unknown"
    assert r["recorded"] is None
    assert r["current"] == "1.2"


def test_unknown_when_current_is_not_given():
    r = check_prompt_version({"prompt_version": "1.0"}, None)
    assert r["status"] == "unknown"
    assert r["current"] is None


def test_unknown_is_not_silently_match():
    """★★ 这一条是那个伪造的正面证据：**不知道 ≠ 通过**。"""
    for rec, cur in (({}, "1.0"), ({"prompt_version": "1.0"}, None), ({}, None)):
        assert check_prompt_version(rec, cur)["status"] != "match", (
            f"缺数据被判成了 match：record={rec!r} current={cur!r} —— "
            f"那是把「不知道」渲染成「通过」，同 §10 第 6 条说的伪造")


def test_status_is_always_in_the_declared_enum():
    """★ 面板上的三值是**可数**的：任何输入都得落进那三个里。"""
    for rec, cur in (({}, None), ({"prompt_version": "1"}, "1"),
                     ({"prompt_version": "1"}, "2"), ({}, "2"),
                     ({"prompt_version": ""}, "2")):
        assert check_prompt_version(rec, cur)["status"] in PROMPT_VERSION_STATUSES


# --------------------------------------------------------------------------
# 那句话：match 与 unknown 都不该发提醒
# --------------------------------------------------------------------------
def test_warning_only_on_stale():
    assert prompt_version_warning({"prompt_version": "1.0"}, "1.2") is not None
    assert prompt_version_warning({"prompt_version": "1.0"}, "1.0") is None


def test_warning_is_none_on_unknown():
    """★★ unknown **不发**提醒是刻意的：一句"我不知道"会让人误以为引擎知道得多。

    ★ 故它该由**要判定的那一层**显式处理，不该在这里变成一句含糊的话。
    """
    assert prompt_version_warning({}, "1.2") is None
    assert prompt_version_warning({"prompt_version": "1.0"}, None) is None


# --------------------------------------------------------------------------
# 与真实产物对接（★ 证明它能吃 build_calibration_record 的输出）
# --------------------------------------------------------------------------
def test_works_on_a_real_formal_record():
    """★ 用真实的 `build_calibration_record` 产一份正式标定，再判它过期。"""
    from direction_drift.calibration.record import build_calibration_record

    calib = {"auc": 0.9, "suggested_low": 0.2, "suggested_high": 0.5,
             "target_precision": 0.95}
    rec = build_calibration_record(
        calibration=calib, mode="formal", source="property-test",
        n=100, n_positive=30, encoder="demo", weights={"cone_alignment": 0.9},
        prompt_version="1.0", card_fingerprint="c2:abc")
    assert rec["prompt_version"] == "1.0"

    assert check_prompt_version(rec, "1.0")["status"] == "match"
    assert check_prompt_version(rec, "1.1")["status"] == "stale"


def test_synthetic_record_can_be_unknown():
    """★ `synthetic` 路径不要求自描述 ⇒ 它很可能**没记** prompt_version ⇒ unknown。

    这一条守的是：**别把那批兜底产物判成 stale**。
    """
    from direction_drift.calibration.record import build_calibration_record

    rec = build_calibration_record(
        calibration={"auc": 0.8, "suggested_low": 0.3, "suggested_high": 0.6},
        mode="synthetic", source="fallback", n=50, n_positive=10)
    assert rec.get("prompt_version") is None
    assert check_prompt_version(rec, "1.0")["status"] == "unknown"


# --------------------------------------------------------------------------
# 探针自检
# --------------------------------------------------------------------------
def test_the_checker_can_fail(tmp_path):
    """核验器自检：证明三档**都真的可能出现**，而不是永远走同一档。"""
    seen = set()
    for cur in ("0.9", "1.1", None):        # ★ 0.9 ⇒ match · 1.1 ⇒ stale · None ⇒ unknown
        seen.add(check_prompt_version({"prompt_version": "0.9"}, cur)["status"])
    assert "match" in seen, "核验器失灵：永远判不出 match"
    assert "stale" in seen, "核验器失灵：永远判不出 stale"
    assert "unknown" in seen, "核验器失灵：永远判不出 unknown"
    assert seen == set(PROMPT_VERSION_STATUSES), (
        f"三档没全覆盖：实得 {sorted(seen)}")

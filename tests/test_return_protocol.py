# -*- coding: utf-8 -*-
"""返回协议：响应不得陈述没发生的事（v2.3.5）。

`act()` 在 `confirmed_drift` 时返回 `frozen` 与一句人读 `message`。二者都是
**对"刚刚发生了什么"的陈述**，因此必须与实际执行的动作一致：

- 传了锥 → 调用了 `cone.freeze()` → `frozen=True`，文案说"已冻结方向"；
- 锥为 `None` → **什么都没冻结** → `frozen=False`，文案不许说"冻结方向"。

此前 `frozen` 被硬编码为 `True`，锥为 None 时 `freeze()` 并未被调用，响应于是在
陈述一件没发生的事。同一条字典里的 `direction` 用了 `cone.goal if cone else ""`，
说明 None 分支本来就被考虑过——这是漏掉的一处，不是设计。

这属于本项目一直在修的那一族：**"我没做"与"我做了"必须可区分**（v2.3.3 修的是
"放弃判定 ≠ 判定通过"，v2.3.4 修的是"声明的检查手段不在场 ≠ 已经查过了"，
这里是"没冻结 ≠ 已冻结"）。
"""
import pytest

from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.return_protocol import DiagnoseConfig, ReturnProtocol

CRITICAL = {"drift_level": "confirmed_drift"}


def _minimal_cone():
    """最小的真锥（离线、确定性）。

    刻意不用 `DirectionCone(goal=...)`——构造器要 axis/aperture/softness，
    手搓那三个值会让测试依赖锥的内部几何而不只是"有个锥可冻结"。
    """
    from direction_drift.utils.demo_encoder import DemoEncoder

    enc = DemoEncoder()
    return DirectionCone.from_samples(
        goal="帮用户规划旅行",
        core_samples=[f"帮用户规划第{i}天旅行行程，考虑天气与预算" for i in range(22)],
        negative_samples=[f"帮我写一段Python代码实现排序算法{i}" for i in range(10)],
        embed_fn=enc.encode)


# --------------------------------------------------- 单元层：act() 自身


def test_no_cone_means_nothing_was_frozen_and_the_message_says_so():
    """无锥可冻结：`frozen` 必须是 False，文案不得宣称已冻结。"""
    proto = ReturnProtocol()
    r = proto.act(dict(CRITICAL), None, None)

    assert r["action"] == "return"
    assert r["frozen"] is False, "没有传锥就什么都没冻结，不许报 True"
    assert "未冻结" in r["message"], r["message"]
    assert "已冻结方向" not in r["message"], r["message"]
    assert proto.state == "returning", "状态迁移与人有没有锥无关"


def test_with_cone_the_freeze_really_happened():
    """传了锥：`frozen` 为 True，且锥上确实落了冻结标记。"""
    cone = _minimal_cone()
    proto = ReturnProtocol()
    r = proto.act(dict(CRITICAL), cone, None)

    assert r["frozen"] is True
    assert "已冻结方向" in r["message"]
    assert cone.frozen is True, "报 frozen=True 就必须真的冻结了"
    assert cone.freeze_reason == "confirmed_drift"


def test_whether_frozen_or_not_a_human_must_decide():
    """冻结与否都改变不了"由人决定"：这条不许被 frozen 的取值带偏。"""
    cone = _minimal_cone()
    for c in (None, cone):
        r = ReturnProtocol().act(dict(CRITICAL), c, None)
        assert r["requires_human"] is True
        assert r["action"] == "return", "action 永远是 return，不是 auto-resume"


# --------------------------------------------------- 集成层：真跑出来的漂移


def _cone(encoder, samples):
    return DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)


def _detector(cone, align_fn, samples):
    """与 test_full_chain 同法标定，保证这条流确实走得到 confirmed_drift。"""
    from direction_drift.calibration.roc import calibrate_thresholds

    scores = [align_fn(cone, s)["overall_alignment"] for s in samples["core"][20:]]
    scores += [align_fn(cone, s)["overall_alignment"] for s in samples["negative"]]
    calib = calibrate_thresholds(scores, [0] * 5 + [1] * 12, target_precision=0.95)
    return DriftDetector(high=calib["suggested_high"], low=calib["suggested_low"],
                         calibrated=True)


def _drive(cone, align_fn, samples, cone_for_act):
    """12 条反面样本喂进一个新的标定 detector，返回最后一条 act 结果。"""
    det = _detector(cone, align_fn, samples)
    proto = ReturnProtocol(DiagnoseConfig(calibrated=True))
    last = None
    for _ in range(12):
        al = align_fn(cone, samples["negative"][0])
        last = proto.act(det.judge(al), cone_for_act, al)
    return last


def test_a_real_confirmed_drift_also_reports_honestly(encoder, samples, align_fn):
    """真跑出来的 confirmed_drift 走同一条口径（不是只有手工构造的字典才对）。"""
    cone = _cone(encoder, samples)

    with_cone = _drive(cone, align_fn, samples, cone)
    assert with_cone["action"] == "return", "这条流应当走到返回动作，否则本测试空转"
    assert with_cone["frozen"] is True
    assert cone.frozen is True

    without = _drive(cone, align_fn, samples, None)
    assert without["action"] == "return"
    assert without["frozen"] is False
    assert "未冻结" in without["message"]


@pytest.mark.parametrize("level,expected_action", [
    ("normal", "continue"),
    ("warning", "observe"),
    ("drift", "pause"),
    ("uncalibrated", "observe"),
    ("insufficient_history", "continue"),
])
def test_other_levels_do_not_claim_any_freeze(level, expected_action):
    """其余级别一律不出现 `frozen` 字样——只有 confirmed_drift 才谈冻结。"""
    proto = ReturnProtocol()
    r = proto.act({"drift_level": level, "history_size": 0, "needed": 5}, None, None)
    assert r["action"] == expected_action
    assert "frozen" not in r

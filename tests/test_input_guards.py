# -*- coding: utf-8 -*-
"""输入守卫测试（v2.3.3 · P0 回归）。

这两条缺陷的共同形态是**静默**：空输出被夹成满分、短回复被判确认漂移。
故这里的断言不只是"值对不对"，还包括"什么都没发生"——
不写窗口、不改状态、不产生 nan。
"""
import math

import numpy as np
import pytest

from direction_drift.core.alignment import AlignmentCalculator
from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import (DEFAULT_MIN_JUDGE_LEN,
                                                 STATE_SCHEMA_VERSION,
                                                 DriftDetector)

WEIGHTS = {"cone_alignment": 0.9, "constraint_satisfaction": 0.0,
           "negative_similarity": 0.1}


@pytest.fixture(scope="module")
def cone(encoder, samples):
    return DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        embed_fn=encoder.encode)


@pytest.fixture(scope="module")
def calc(encoder):
    return AlignmentCalculator(encoder=encoder)


# ---------------- P0-1 空输出 / 零向量 ----------------

@pytest.mark.parametrize("text", ["", "   ", "\n\t ", "\u3000"])
def test_empty_output_is_invalid_not_perfect(calc, cone, text):
    """空输出必须是"无分数"，绝不能被夹成 1.0（nan 比较恒 False 的陷阱）。"""
    r = calc.compute(cone, text)
    assert r["invalid"] is True
    assert r["reason"] == "empty_output"
    assert r["overall_alignment"] is None      # 不是 0.0，也不是 1.0
    assert r["cone_alignment"] is None
    assert r["cone_position"] == "unknown"


def test_no_nan_leaks_from_any_input(calc, cone):
    """任何输入都不许产出 nan 分数（nan 会一路传染到看板）。"""
    for text in ["", " ", "嗯", "！！！", "正常的一句话回复内容"]:
        r = calc.compute(cone, text)
        for key in ("cone_alignment", "negative_similarity", "overall_alignment"):
            v = r[key]
            assert v is None or not math.isnan(float(v)), f"{key} 出现 nan（输入={text!r}）"


def test_zero_vector_embedding_also_invalid(calc, cone):
    """非空但编码器退化（返回零向量）时同样按 invalid 处置，而不是静默取满分。"""
    class Degenerate:
        def encode(self, text):
            return np.zeros(8)

    r = AlignmentCalculator(encoder=Degenerate()).compute(cone, "有内容但编码器返回零向量")
    assert r["invalid"] is True
    assert r["reason"] == "zero_vector_embedding"


def test_cone_contains_rejects_zero_vector(cone):
    with pytest.raises(ValueError, match="零向量"):
        cone.contains(np.zeros(len(cone.axis)))


def test_from_samples_rejects_zero_vector_boundary(encoder, samples):
    """零向量边界样本在建锥阶段就要炸，不能带着 nan 往下走。"""
    bad = list(samples["boundary"]) + [""]
    with pytest.raises(ValueError, match="零向量边界样本"):
        DirectionCone.from_samples(
            goal="g", core_samples=samples["core"], boundary_samples=bad,
            embed_fn=encoder.encode)


def test_judge_marks_no_output_and_leaves_window_untouched(calc, cone):
    det = DriftDetector(window=2, calibrated=True)
    r = det.judge(calc.compute(cone, ""))
    assert r["drift_level"] == "no_output"
    assert r["score"] is None and r["pending_verification"] is True
    assert len(det.history) == 0 and det.state == "warmup"


# ---------------- P0-2 短输入不参与判定 ----------------

@pytest.mark.parametrize("text", ["好的", "收到", "嗯嗯", "谢谢", "在吗"])
def test_short_replies_are_not_judged(calc, cone, text):
    """客服/陪伴场景最常见的合法短回复，不得被判成漂移。"""
    det = DriftDetector(window=1, high=0.6, low=0.4, calibrated=True)
    r = det.judge(calc.compute(cone, text, weights=WEIGHTS))
    assert r["drift_level"] == "too_short"
    assert r["text_len"] < DEFAULT_MIN_JUDGE_LEN
    assert len(det.history) == 0                # 不污染窗口


def test_short_flood_cannot_trigger_confirmed_drift(calc, cone):
    """连续短回复曾足以把窗口填满并推到 confirmed_drift → return → 冻结光锥。"""
    det = DriftDetector(window=3, high=0.6, low=0.4, calibrated=True)
    levels = [det.judge(calc.compute(cone, "嗯嗯", weights=WEIGHTS))["drift_level"]
              for _ in range(10)]
    assert set(levels) == {"too_short"}
    assert len(det.history) == 0


def test_long_output_still_judged_normally(calc, cone):
    """守卫只挡短输入，正常长度的在轨句不受影响。"""
    det = DriftDetector(window=2, high=0.6, low=0.4, calibrated=True)
    text = "我们先确定目的地和天数，再逐日规划行程，考虑天气与预算"
    lvl = None
    for _ in range(2):
        lvl = det.judge(calc.compute(cone, text, weights=WEIGHTS))["drift_level"]
    assert lvl in ("normal", "warning")
    assert len(det.history) == 2


def test_min_judge_len_is_configurable_and_serialized(calc, cone):
    det = DriftDetector(window=1, calibrated=True, min_judge_len=0)
    # 关掉守卫后短句会被照常判定（场景可自行决定，但要显式）
    assert det.judge(calc.compute(cone, "好的", weights=WEIGHTS))["drift_level"] != "too_short"

    det2 = DriftDetector(window=3, calibrated=True, min_judge_len=9)
    snap = det2.to_dict()
    assert snap["config"]["min_judge_len"] == 9
    assert snap["schema_version"] == STATE_SCHEMA_VERSION
    assert DriftDetector.from_dict(snap).min_judge_len == 9


def test_judgement_identical_across_restore_with_guard(calc, cone):
    """守卫不破坏"中断恢复后逐条判定与未中断分支完全一致"这条纪律。"""
    long_text = "我们先确定目的地和天数，再逐日规划行程，考虑天气与预算"
    stream = [long_text, "嗯嗯", long_text, "", long_text, "好的", long_text, long_text]

    a = DriftDetector(window=2, high=0.6, low=0.4, calibrated=True)
    ra = [a.judge(calc.compute(cone, t, weights=WEIGHTS)) for t in stream]
    b = DriftDetector.from_dict(a.__class__(window=2, high=0.6, low=0.4,
                                            calibrated=True).to_dict())
    rb = [b.judge(calc.compute(cone, t, weights=WEIGHTS)) for t in stream]
    for x, y in zip(ra, rb):
        assert x["drift_level"] == y["drift_level"]
        assert x.get("score") == y.get("score")


def test_return_protocol_does_not_act_on_insufficient_input(calc, cone):
    """输入不足时返回协议只观察，绝不暂停/返回/冻结。"""
    from direction_drift.core.return_protocol import ReturnProtocol

    proto = ReturnProtocol()
    det = DriftDetector(window=1, calibrated=True)
    for text in ["", "嗯", "好的"]:
        al = calc.compute(cone, text, weights=WEIGHTS)
        act = proto.act(det.judge(al), cone, al)
        assert act["action"] in ("continue", "observe")
    assert not cone.frozen and proto.state == "running"

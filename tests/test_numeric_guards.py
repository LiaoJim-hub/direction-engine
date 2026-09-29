# -*- coding: utf-8 -*-
"""数值守卫测试（v2.3.2）：量程自洽检查 + 光锥锥轴退化保护。

为什么这两组值得单独测：

- **量程不自洽是"静默全量误判"型缺陷**——不抛异常、不报错，只是结论整体错。
  所以必须有一条测试把"默认权重 + 默认阈值不自洽"这个**事实**钉住：
  将来若有人为了让数字好看而调大默认权重、或悄悄改掉默认阈值，
  测试会失败并要求正面处理，而不是让陷阱静默消失。
- **锥轴退化会静默产生 nan** 并污染其后所有判定，必须在建锥时就拦住。
"""
import warnings

import numpy as np
import pytest

from direction_drift.core.alignment import (DEFAULT_WEIGHTS,
                                            check_range_consistency,
                                            overall_ceiling)
from direction_drift.core.direction_cone import DirectionCone

# demo / 已转正标定所用的权重形态：正项为主、和归一
CALIBRATED_WEIGHTS = {"cone_alignment": 0.9, "constraint_satisfaction": 0.0,
                      "negative_similarity": 0.1}


# ---------- 量程自洽检查 ----------

def test_default_weights_ceiling_equals_cone_weight():
    """约束项只罚不奖、负相似度是减项 → 上界只看 cone_alignment 权重。"""
    assert overall_ceiling(DEFAULT_WEIGHTS) == pytest.approx(0.4)
    assert overall_ceiling(CALIBRATED_WEIGHTS) == pytest.approx(0.9)


def test_default_weight_threshold_pair_is_inconsistent():
    """回归防线：默认权重配默认阈值不自洽——high 永远够不到，且贴着 low。

    这条断言"不自洽"是刻意的：它是本仓已知的量程陷阱，不是 bug 修复目标。
    """
    r = check_range_consistency(DEFAULT_WEIGHTS, high=0.6, low=0.4)
    assert r["consistent"] is False
    assert r["ceiling"] == pytest.approx(0.4)
    assert any("永远不可能触发" in x for x in r["reasons"])
    assert any("贴着低分界" in x for x in r["reasons"])


def test_calibrated_pair_is_consistent():
    """同一份标定产出的权重 + 阈值 → 自洽（这是接入方应达到的状态）。"""
    r = check_range_consistency(CALIBRATED_WEIGHTS, high=0.407, low=0.207)
    assert r["consistent"] is True, r["reasons"]


def test_weight_sum_drift_is_surfaced():
    """权重和不为 1 会让量程随权重和漂移，应被显形。"""
    r = check_range_consistency({"cone_alignment": 0.5,
                                 "constraint_satisfaction": 0.5,
                                 "negative_similarity": 0.5},
                                high=0.3, low=0.2)
    assert any("权重之和" in x for x in r["reasons"])


def test_check_does_not_mutate_inputs():
    """检查器只显形：不修改传入的权重字典。"""
    w = dict(CALIBRATED_WEIGHTS)
    before = dict(w)
    check_range_consistency(w, high=0.407, low=0.207)
    assert w == before


# ---------- 光锥锥轴退化保护 ----------

def _half_opposed(text: str) -> np.ndarray:
    """核心样本一半指 +x、一半指 −x → 均值恰为零向量（方向互相抵消）。"""
    i = int(text.split("_")[1])
    v = np.zeros(4)
    v[0] = 1.0 if i % 2 == 0 else -1.0
    return v


def _spread(text: str) -> np.ndarray:
    """方向铺开约 324 度 → 均值范数很低（<0.3）但不为零：应警告而非阻断。"""
    i = int(text.split("_")[1])
    theta = 1.8 * np.pi * i / 19.0
    return np.array([np.cos(theta), np.sin(theta)])


def _zero(text: str) -> np.ndarray:
    return np.zeros(4)


CORE_20 = [f"s_{i}" for i in range(20)]


def test_opposed_core_rejected():
    with pytest.raises(ValueError, match="方向退化"):
        DirectionCone.from_samples(goal="g", core_samples=CORE_20,
                                   embed_fn=_half_opposed)


def test_zero_vector_core_rejected():
    with pytest.raises(ValueError, match="零向量"):
        DirectionCone.from_samples(goal="g", core_samples=CORE_20,
                                   embed_fn=_zero)


def test_dispersed_core_warns_but_builds():
    """一致性偏低只警告、不阻断——与"边界样本夹角过大"的处理保持一致。"""
    with pytest.warns(UserWarning, match="方向一致性"):
        cone = DirectionCone.from_samples(goal="g", core_samples=CORE_20,
                                          embed_fn=_spread)
    assert cone.core_sample_count == 20
    assert np.isfinite(cone.axis).all(), "锥轴不应出现 nan"
    assert float(np.linalg.norm(cone.axis)) == pytest.approx(1.0, abs=1e-6)


def test_healthy_core_builds_without_warning():
    """正常样本不应产生方向类警告（避免守卫变成噪声）。"""
    def _coherent(text: str) -> np.ndarray:
        i = int(text.split("_")[1])
        theta = 0.2 * np.pi * (i / 19.0)          # 集中在 36 度内
        return np.array([np.cos(theta), np.sin(theta)])

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        cone = DirectionCone.from_samples(goal="g", core_samples=CORE_20,
                                          embed_fn=_coherent)
    assert cone.core_sample_count == 20

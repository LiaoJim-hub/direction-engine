# -*- coding: utf-8 -*-
"""标定层测试：calibrate_thresholds（含 v2.2.1 修复）+ scan_operating_points。"""
import numpy as np
import pytest

from direction_drift.calibration.roc import calibrate_thresholds, scan_operating_points


def test_requires_two_classes():
    with pytest.raises(ValueError):
        calibrate_thresholds([0.9, 0.8, 0.7], [0, 0, 0])


def test_separable_scores_get_midpoint_threshold():
    # v2.2.1 场景：类间完全可分（反面 alignment 截断为 0 → drift=1.0 撞顶），
    # PR 阈值退化到端点。修复后取两类 drift 中点，suggested_low 不为 0。
    scores = [0.9, 0.85, 0.8, 0.75, 0.7] + [0.0, 0.0, 0.0, 0.0]
    labels = [0, 0, 0, 0, 0] + [1, 1, 1, 1]
    calib = calibrate_thresholds(scores, labels, target_precision=0.95)
    assert calib["auc"] == 1.0
    assert 0.0 < calib["suggested_low"] < 0.75


def test_tied_thresholds_take_lowest_not_zero():
    # v2.2.1 场景：并列达标时取最小阈值（查全最大），而不是把 low 压到 0。
    # 构造：正常类 drift ∈ [0, 0.1]，漂移类含大量 drift=1.0 的截断并列 + 少量中间值。
    rng = np.random.default_rng(7)
    scores = list(0.95 - rng.uniform(0, 0.1, 30)) + \
             [0.5] + [0.0] * 10
    labels = [0] * 30 + [1] * 11
    calib = calibrate_thresholds(scores, labels, target_precision=0.9)
    assert calib["suggested_low"] > 0.1


def test_scan_operating_points_shape_and_order():
    rows = scan_operating_points(
        [0.9, 0.8, 0.3, 0.2, 0.1, 0.05], [0, 0, 1, 1, 1, 1])
    assert rows, "扫描表不应为空"
    lows = [r["low"] for r in rows]
    assert lows == sorted(lows)                    # 候选阈值升序扫描
    for r in rows:
        assert {"low", "recall", "recall_rate", "false_positives",
                "precision"} <= set(r.keys())


def test_scan_operating_points_extreme_is_safe():
    # 阈值放到极低：全部报漂移 → 查全 2/2、误标=全部正常样本
    rows = scan_operating_points([0.9, 0.8, 0.3, 0.2], [0, 0, 1, 1],
                                 candidates=[0.05, 0.95])
    bottom = rows[-1]
    assert bottom["recall"] == "2/2"
    assert bottom["false_positives"] == 2
    # 高阈值端：一只都抓不到
    assert rows[0]["recall"] == "0/2" and rows[0]["false_positives"] == 0

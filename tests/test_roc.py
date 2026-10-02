# -*- coding: utf-8 -*-
"""标定层测试：calibrate_thresholds（含 v2.2.1 修复）+ scan_operating_points。"""
import numpy as np
import pytest

from direction_drift.calibration.record import build_calibration_record
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


def test_unachievable_precision_does_not_fall_back_to_a_fake_threshold():
    """达标不可得时**不许**回落成一个看起来正常的数字。

    出处：挑刺报告 P1-6。原形态 `else: t, ap, ar = 0.5, None, None`——
    而默认 `low=0.4` / `high=0.6`，0.5 恰好落在两者中间，**长得完全像标定结果**。
    于是"这次没算出阈值"会被读成"标定结果是 0.5"，一路写进产物用下去。

    它与 P0-2（两侧皆空 → consistent）是同一形：**fallback 值伪装成测量值**。
    修法也同形——给不出来就给 None，并把为什么给不出来一并带出去。
    """
    # 两类严重重叠：无论阈值取在哪，精确率都上不去
    scores = [0.50, 0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57]
    labels = [0, 1, 0, 1, 0, 1, 0, 1]
    out = calibrate_thresholds(scores, labels, target_precision=0.99)

    assert out["achievable"] is False, out
    assert out["suggested_low"] is None, out          # 不是 0.5，是"没有"
    assert out["suggested_high"] is None, out
    assert isinstance(out.get("reason"), str) and out["reason"], out
    # witness：原因里要说清目标准确率与本次实际最好值
    assert "0.99" in out["reason"], out["reason"]


def test_unachievable_calibration_cannot_be_written_as_a_record():
    """算不出阈值的标定**不许落成产物**——宁可失败，也不留一个假数字。

    对照组：显式给了 low / high（人工选点）时允许写入——那是另一种合法来源，
    不是回落值。两格都要钉住，否则"禁止假阈值"会顺手把人工选点也堵死。
    """
    scores = [0.50, 0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57]
    labels = [0, 1, 0, 1, 0, 1, 0, 1]
    calib = calibrate_thresholds(scores, labels, target_precision=0.99)

    with pytest.raises(ValueError, match="没能给出可用阈值"):
        build_calibration_record(
            calibration=calib, mode="synthetic", source="t", n=len(scores),
            n_positive=sum(labels), encoder="e", weights=None,
            prompt_version="p", card_fingerprint="c2:abc")

    # 人工选定操作点：low / high 都是显式给的，允许写入
    rec = build_calibration_record(
        calibration=calib, mode="formal", source="t", n=len(scores),
        n_positive=sum(labels), encoder="e", weights={"cone": 0.4},
        prompt_version="p", card_fingerprint="c2:abc",
        low=0.125, high=0.132,
        label_provenance="人工标注", recall_at_low="4/4",
        operating_point="人工选点")
    assert rec["low"] == 0.125 and rec["high"] == 0.132

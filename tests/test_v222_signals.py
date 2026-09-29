# -*- coding: utf-8 -*-
"""v2.2.2 信号测试（自 smoke_v222.py 转正）：CUSUM（修正版）+ 置信标注。

哲学门控红线：CUSUM 只允许 normal→warning 提级，绝不越级到 drift/confirmed_drift，
绝不触发自动返回。"""
from direction_drift.core.drift_detector import DriftDetector


def make(aligned_score, pos="core"):
    return {"overall_alignment": aligned_score, "cone_position": pos}


def test_normal_run_no_alarm():
    det = DriftDetector(calibrated=True, high=0.6, low=0.4, cusum_h=0.5)
    for _ in range(12):
        r = det.judge(make(0.65))
    assert r["drift_level"] == "normal" and not r["cusum_alarm"]
    assert r["confidence"] == "high"


def test_cusum_catches_slow_drift_below_threshold():
    # 分数 0.61 ≥ high=0.6（普通规则判 normal 抓不到），但持续低于受控均值 0.65，
    # CUSUM 逐步累积亏损并越过 h=0.3 → 告警，且只提级到 warning。
    det = DriftDetector(calibrated=True, high=0.6, low=0.4,
                        cusum_target=0.65, cusum_k=0.01, cusum_h=0.3)
    for _ in range(det.window - 1):        # 跳过预热（insufficient_history 无 cusum 字段）
        det.judge(make(0.61))
    levels, alarms = [], []
    for _ in range(14):
        r = det.judge(make(0.61))
        levels.append(r["drift_level"])
        alarms.append(r["cusum_alarm"])
    assert any(alarms)
    i = alarms.index(True)
    assert levels[i] == "warning"
    assert set(levels) <= {"normal", "warning"}     # 绝不越级


def test_cusum_alarm_resets_on_recovery():
    det = DriftDetector(calibrated=True, high=0.6, low=0.4, cusum_h=0.4)
    for _ in range(det.window - 1):
        det.judge(make(0.50))
    alarms = [det.judge(make(0.50))["cusum_alarm"] for _ in range(10)]
    assert any(alarms)
    for _ in range(6):
        r = det.judge(make(0.65))          # 恢复正常：CUSUM 快速归零
    assert not r["cusum_alarm"] and r["cusum_s"] == 0.0
    assert r["drift_level"] == "normal"


def test_uncalibrated_no_cusum():
    det = DriftDetector(calibrated=False)
    for _ in range(6):
        r = det.judge(make(0.1))
    assert r["drift_level"] == "uncalibrated"
    assert r["cusum_s"] == 0.0 and not r["cusum_alarm"]
    assert r["confidence"] == "low"


def test_confidence_label_follows_cone_position():
    det = DriftDetector(calibrated=True, high=0.6, low=0.4)
    for _ in range(11):
        det.judge(make(0.65))
    c_core = det.judge(make(0.65))["confidence"]
    c_bnd = det.judge(make(0.65, "boundary"))["confidence"]
    c_out = det.judge(make(0.65, "outside"))["confidence"]
    assert (c_core, c_bnd, c_out) == ("high", "medium", "low")


def test_reset_clears_cusum_state():
    det = DriftDetector(calibrated=True, high=0.6, low=0.4)
    for _ in range(11):
        det.judge(make(0.65))
    det.reset()
    assert det._cusum_s == 0.0

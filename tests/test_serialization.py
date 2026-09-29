# -*- coding: utf-8 -*-
"""状态序列化测试（v2.2.4）。

这是托管层「服务重启后会话不失真」的回归防线：快照要能过 JSON、恢复后判定
必须与未中断的分支**逐条完全一致**（窗口不丢、CUSUM 累积量不归零）。
"""
import json

import numpy as np
import pytest

from direction_drift.calibration.roc import calibrate_thresholds
from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import (STATE_SCHEMA_VERSION as DET_SV,
                                                 DriftDetector)
from direction_drift.core.return_protocol import (STATE_SCHEMA_VERSION as PROTO_SV,
                                                   DiagnoseConfig, ReturnProtocol)
from direction_drift.core.session_state import (STATE_SCHEMA_VERSION, restore,
                                                snapshot)


def _cone(encoder, samples):
    return DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)


def _calibrated(cone, align_fn, samples):
    scores = [align_fn(cone, s)["overall_alignment"] for s in samples["core"][20:]]
    scores += [align_fn(cone, s)["overall_alignment"] for s in samples["negative"]]
    calib = calibrate_thresholds(scores, [0] * 5 + [1] * 12, target_precision=0.95)
    return DriftDetector(high=calib["suggested_high"], low=calib["suggested_low"],
                         calibrated=True)


# ---- DriftDetector ----

def test_detector_roundtrip_is_step_for_step_identical(encoder, samples, align_fn):
    """从中断点恢复后，后续每一步的判定字段必须与未中断分支完全相同。"""
    cone = _cone(encoder, samples)
    a = _calibrated(cone, align_fn, samples)
    seq = ([samples["core"][0]] * 2 + [samples["negative"][0]] * 10)

    for t in seq[:4]:                       # 中断前：先积累一段轨迹
        a.judge(align_fn(cone, t))
    b = DriftDetector.from_dict(a.to_dict())   # ← 模拟重启恢复
    assert b.config_fingerprint() == a.config_fingerprint()
    assert list(b.history) == list(a.history)
    assert b._cusum_s == a._cusum_s
    assert b.state == a.state

    for t in seq[4:]:
        ra, rb = a.judge(align_fn(cone, t)), b.judge(align_fn(cone, t))
        for key in ("drift_level", "score", "mean_score", "slope", "low_ratio",
                    "cusum_s", "cusum_alarm", "confidence", "history_size",
                    "pending_verification"):
            assert ra[key] == rb[key], f"恢复后字段 {key} 偏离：{ra[key]} vs {rb[key]}"


def test_restored_detector_does_not_restart_warmup(encoder, samples, align_fn):
    """痛点回归：不恢复 = 重启后窗口清零、判定失真；恢复 = 立刻正常判定。"""
    cone = _cone(encoder, samples)
    det = _calibrated(cone, align_fn, samples)
    for _ in range(det.window):
        det.judge(align_fn(cone, samples["core"][0]))

    fresh = DriftDetector(high=det.high, low=det.low, calibrated=True)
    assert fresh.judge(align_fn(cone, samples["core"][0]))["drift_level"] \
        == "insufficient_history"

    revived = DriftDetector.from_dict(det.to_dict())
    assert revived.judge(align_fn(cone, samples["core"][0]))["drift_level"] \
        in ("normal", "warning")


def test_deque_limits_preserved_after_restore():
    d = DriftDetector(window=3, high=0.8, low=0.3, calibrated=True)
    for i in range(60):
        d.judge({"overall_alignment": 0.5 + 0.001 * i, "cone_position": "core"})
    assert len(d.history) == 50 and len(d.means) == 20     # maxlen 生效

    e = DriftDetector.from_dict(d.to_dict())
    assert e.history.maxlen == 50 and len(e.history) == 50
    assert e.means.maxlen == 20 and len(e.means) == 20
    assert list(e.means) == list(d.means)


def test_archive_survives_roundtrip():
    d = DriftDetector(window=2, high=0.8, low=0.3, calibrated=True)
    for i in range(4):
        d.judge({"overall_alignment": 0.5, "cone_position": "core"})
    d.reset()                                        # 历史移入审计归档
    e = DriftDetector.from_dict(d.to_dict())
    assert len(e.archive) == 1 and e.archive == d.archive
    assert len(e.archive[0]) == 4 and e.state == "warmup"


def test_flat_and_legacy_snapshot_accepted():
    flat = {"window": 4, "high": 0.7, "low": 0.35, "calibrated": True,
            "history": [{"score": 0.6, "position": "core", "ts": "2026-01-01T00:00:00"}],
            "state": "running"}
    d = DriftDetector.from_dict(flat)
    assert d.window == 4 and d.calibrated and len(d.history) == 1
    assert d.state == "running"


def test_unknown_fields_ignored_and_defaults_kept():
    d = DriftDetector.from_dict({"schema_version": 1,
                                 "config": {"window": 5, "future_flag": True},
                                 "runtime": {}})
    assert d.window == 5 and d.low == 0.4 and not hasattr(d, "future_flag")


def test_future_schema_version_rejected():
    with pytest.raises(ValueError):
        DriftDetector.from_dict({"schema_version": DET_SV + 1, "config": {}, "runtime": {}})
    with pytest.raises(ValueError):
        ReturnProtocol.from_dict({"schema_version": PROTO_SV + 1})
    with pytest.raises(ValueError):
        restore({"schema_version": STATE_SCHEMA_VERSION + 1})


def test_illegal_or_bad_input_rejected():
    with pytest.raises(ValueError):
        DriftDetector.from_dict({"config": {"window": 0}, "runtime": {}})
    with pytest.raises(TypeError):
        DriftDetector.from_dict(["not", "a", "dict"])
    with pytest.raises(TypeError):
        ReturnProtocol.from_dict(None)


# ---- ReturnProtocol ----

def test_protocol_roundtrip_and_state_discipline():
    p = ReturnProtocol(DiagnoseConfig(calibrated=True, cone_align_low=0.5))
    p.state = "returning"
    q = ReturnProtocol.from_dict(p.to_dict())
    assert q.state == "returning"                       # 冻结/返回态被如实恢复
    assert q.diag.calibrated and q.diag.cone_align_low == 0.5

    # 未知 state 回落到 running：不凭不认识的字段值进入观察/返回态
    assert ReturnProtocol.from_dict({"state": "zombie"}).state == "running"
    # 恢复 returning 不等于恢复运行，resume 才是唯一的出口
    assert q.state == "returning"


# ---- DirectionCone 时间戳（v2.2.4 补齐）----

def test_cone_timestamps_survive_roundtrip(encoder, samples):
    cone = _cone(encoder, samples)
    cone.freeze("confirmed_drift")
    c2 = DirectionCone.from_dict(cone.to_dict())
    assert c2.frozen and c2.freeze_reason == "confirmed_drift"
    assert c2.frozen_at is not None and cone.frozen_at is not None
    assert abs((c2.frozen_at - cone.frozen_at).total_seconds()) < 1
    assert np.allclose(c2.axis, cone.axis)


# ---- 会话快照（托管层入口）----

def test_session_snapshot_is_json_safe_and_roundtrips(encoder, samples, align_fn):
    cone = _cone(encoder, samples)
    det = _calibrated(cone, align_fn, samples)
    proto = ReturnProtocol(DiagnoseConfig(calibrated=True))
    for _ in range(det.window):
        det.judge(align_fn(cone, samples["core"][0]))

    blob = snapshot(cone, det, proto,
                    extra={"tenant": "demo", "prompt_version": "4.2"})
    text = json.dumps(blob)                    # Redis 里存的就是这个字符串
    parts = restore(json.loads(text))

    assert isinstance(parts["cone"], DirectionCone)
    assert parts["detector"].config_fingerprint() == det.config_fingerprint()
    assert list(parts["detector"].history) == list(det.history)
    assert parts["detector"]._cusum_s == det._cusum_s
    assert np.allclose(parts["cone"].axis, cone.axis)
    assert parts["protocol"].diag.calibrated
    assert parts["extra"]["prompt_version"] == "4.2"


def test_snapshot_is_a_copy_not_a_handle(encoder, samples, align_fn):
    cone = _cone(encoder, samples)
    det = _calibrated(cone, align_fn, samples)
    blob = snapshot(cone, det, ReturnProtocol())
    before = len(det.history)
    restore(blob)["detector"].judge({"overall_alignment": 0.1, "cone_position": "core"})
    assert len(det.history) == before          # 改副本不影响原对象


def test_snapshot_missing_section_rejected():
    with pytest.raises(ValueError):
        restore({"schema_version": 1, "cone": {}, "detector": {}})
    with pytest.raises(TypeError):
        restore("not a dict")


def test_snapshot_type_guard():
    with pytest.raises(TypeError):
        snapshot({"not": "a cone"}, None, None)

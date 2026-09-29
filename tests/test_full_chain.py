# -*- coding: utf-8 -*-
"""全链路测试（自 smoke_direction_drift.py 转正）：
建锥 → 对齐+约束 → 未标定不判定 → 标定后判定 → 返回协议 → 序列化往返。"""
import numpy as np
import pytest

from direction_drift import __version__
from direction_drift.calibration.roc import calibrate_thresholds
from direction_drift.core.constraint_checker import ConstraintChecker
from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.return_protocol import DiagnoseConfig, ReturnProtocol


def test_version():
    """版本号必须与 pyproject.toml 一致（防只改一处导致发布版本错位）。"""
    import pathlib
    import re

    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    toml = (pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in toml


def test_build_cone(encoder, samples):
    cone = DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)
    assert not cone.frozen
    assert cone.core_sample_count == 25
    assert np.isclose(np.linalg.norm(cone.axis), 1.0)


def test_alignment_separates_on_topic_from_off_topic(encoder, samples, align_fn):
    cone = DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)
    a_in = align_fn(cone, "我们先确定目的地和天数，再规划每日行程")
    a_out = align_fn(cone, "我直接帮你写一段Python代码实现快速排序吧")
    assert a_in["overall_alignment"] > a_out["overall_alignment"]


def test_constraint_checker():
    chk = ConstraintChecker()
    r_hit = chk.check("我推荐XX酒店，直接订了", ["不直接推荐具体酒店"])
    r_neg = chk.check("我不会推荐具体酒店", ["不直接推荐具体酒店"])
    assert r_hit["satisfaction"] < 0
    assert r_neg["satisfaction"] == 0


def _calibrated_detector(cone, align_fn, samples):
    scores = [align_fn(cone, s)["overall_alignment"] for s in samples["core"][20:]]
    scores += [align_fn(cone, s)["overall_alignment"] for s in samples["negative"]]
    labels = [0] * 5 + [1] * 12
    calib = calibrate_thresholds(scores, labels, target_precision=0.95)
    assert 0.5 <= calib["auc"] <= 1.0
    det = DriftDetector(high=calib["suggested_high"], low=calib["suggested_low"],
                        calibrated=True)
    return det, calib


def test_uncalibrated_detector_does_not_judge(encoder, samples, align_fn):
    cone = DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)
    det = DriftDetector(calibrated=False)
    for _ in range(det.window):        # 预热窗口：前 window 次返回 insufficient_history
        d = det.judge(align_fn(cone, samples["core"][0]))
    assert d["drift_level"] == "uncalibrated"


def test_calibrated_judgement_direction(encoder, samples, align_fn):
    cone = DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)
    det, _ = _calibrated_detector(cone, align_fn, samples)
    for _ in range(det.window):        # 预热窗口：跳过 insufficient_history 期
        lvl_pos = det.judge(align_fn(cone, samples["core"][0]))["drift_level"]
    det_neg = DriftDetector(high=det.high, low=det.low, calibrated=True)
    for _ in range(det_neg.window):
        lvl_neg = det_neg.judge(align_fn(cone, samples["negative"][0]))["drift_level"]
    assert lvl_pos in ("normal", "warning")
    assert lvl_neg in ("drift", "confirmed_drift")


def test_return_protocol_freeze_and_resume(encoder, samples, align_fn):
    cone = DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)
    det, _ = _calibrated_detector(cone, align_fn, samples)
    proto = ReturnProtocol(DiagnoseConfig(calibrated=True))

    r_ok = proto.act(det.judge(align_fn(cone, samples["core"][0])), cone,
                     align_fn(cone, samples["core"][0]))
    assert r_ok["action"] in ("continue", "observe")

    acts = []
    for _ in range(12):
        al = align_fn(cone, samples["negative"][0])
        acts.append(proto.act(det.judge(al), cone, al)["action"])
    assert "return" in acts and cone.frozen

    back = proto.resume(cone, det)
    assert back["action"] == "continue"
    assert not cone.frozen and len(det.archive) == 1


def test_serialization_roundtrip(encoder, samples):
    cone = DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)
    d2 = cone.to_dict()
    cone2 = DirectionCone.from_dict(d2)
    assert np.allclose(cone.axis, cone2.axis)
    assert cone2.rebuild_count == cone.rebuild_count

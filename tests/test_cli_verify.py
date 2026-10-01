# -*- coding: utf-8 -*-
"""`de verify` 的 CLI 判据测试（PRD §8.1 T2）。

判据的核心是**退出码**：`0` 与 `2` 的区别必须能被脚本区分，否则 CI 会把
"换了引擎版本"误报成"回归"。这里逐码断言，不只看 stdout 文字。
"""

import copy
import json

import pytest

from direction_drift.cli import main
from direction_drift.verify import EXIT_CODES

_BASE = {
    "artifact_schema_version": "1",
    "engine": {"name": "direction-drift", "version": "2.5.0"},
    "card": {"card_id": "c1", "fingerprint": "c2:aaaa", "fingerprint_algo": "c2",
             "fingerprint_digest": "aaaa",
             "fingerprint_status": "recorded_at_calibration"},
    "encoder": {"name": "demo", "weights_hash": None},
    "calibration": {"mode": "formal", "weights": {"high": 1.0},
                    "card_fingerprint": "c2:aaaa"},
    "detector": {"config": {"window": 5}},
    "judgment": {"texts_digest": "t1"},
    "items": [{"score": 0.1, "drift_level": "on_direction", "suspect": False}],
}


def _write(tmp_path, name, over=None):
    art = copy.deepcopy(_BASE)
    if over:
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(art.get(k), dict):
                art[k].update(v)
            else:
                art[k] = v
    p = tmp_path / name
    p.write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_verify_identical_returns_zero(tmp_path):
    a = _write(tmp_path, "a.json")
    rc = main(["verify", "--a", a, "--b", a])
    assert rc == EXIT_CODES["consistent"] == 0


def test_verify_engine_version_differs_returns_two_not_one(tmp_path):
    """`2` 不是失败，是"条件不同、结论不可对话"。判错成 `1` 就会误报回归。"""
    a = _write(tmp_path, "a.json")
    b = _write(tmp_path, "b.json", {"engine": {"name": "direction-drift",
                                               "version": "2.4.0"}})
    rc = main(["verify", "--a", a, "--b", b])
    assert rc == EXIT_CODES["not_comparable"] == 2
    assert rc != EXIT_CODES["mismatch"]


def test_verify_score_beyond_tolerance_returns_one(tmp_path):
    a = _write(tmp_path, "a.json")
    b = _write(tmp_path, "b.json",
               {"items": [{"score": 0.5, "drift_level": "on_direction",
                           "suspect": False}]})
    rc = main(["verify", "--a", a, "--b", b])
    assert rc == EXIT_CODES["mismatch"] == 1


def test_verify_reports_residuals_not_just_pass_fail(tmp_path, capsys):
    """纪律：必须列出逐条残差，不得只给一个通过/不通过。"""
    a = _write(tmp_path, "a.json")
    b = _write(tmp_path, "b.json",
               {"items": [{"score": 0.5, "drift_level": "on_direction",
                           "suspect": False}]})
    main(["verify", "--a", a, "--b", b])
    out = capsys.readouterr().out
    assert "max_abs_delta" in out, "没打印残差：决定权被从人手里拿走了"
    assert "items[0]" in out


def test_verify_missing_file_is_input_error(tmp_path):
    """文件缺失属用法/输入错误（3），不是环境不可得（4）——不许混。"""
    a = _write(tmp_path, "a.json")
    rc = main(["verify", "--a", a, "--b", str(tmp_path / "不存在.json")])
    assert rc == EXIT_CODES["input_error"] == 3


def test_verify_replay_is_not_silently_degraded(tmp_path, capsys):
    """模式 B 未实现就直说，不静默降级成"用别的办法算了一个"。"""
    a = _write(tmp_path, "a.json")
    rc = main(["verify", "--replay", a])
    assert rc == EXIT_CODES["input_error"] == 3
    assert "尚未实现" in capsys.readouterr().err


def test_verify_writes_machine_readable_report(tmp_path):
    a = _write(tmp_path, "a.json")
    out_json = tmp_path / "report.json"
    rc = main(["verify", "--a", a, "--b", a, "--json", str(out_json)])
    assert rc == 0
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["verdict"] == "consistent"
    assert doc["exit_code_equivalent"] == 0
    assert isinstance(doc["checks"], list) and doc["checks"]


def test_verify_declares_score_not_reproducible_when_weights_hash_null(
        tmp_path, capsys):
    """§8.5：权重哈希不可得时，报告不得写成"复算成功"。"""
    a = _write(tmp_path, "a.json")
    main(["verify", "--a", a, "--b", a])
    out = capsys.readouterr().out
    assert "分数可复现未证" in out
    # 断言的是**结论行**：warn 文案里本就含"不得表述为复算成功"这一否定式，
    # 把它当成"说了复算成功"是误判——要查的是结论有没有宣称成功。
    conclusion = next(l for l in out.splitlines() if l.startswith("复算结论"))
    assert "复算成功" not in conclusion, f"结论不得宣称复算成功：{conclusion}"


def test_verify_subcommand_is_registered():
    from direction_drift.cli import build_parser

    help_text = build_parser().format_help()
    assert "verify" in help_text

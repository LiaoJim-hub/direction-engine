# -*- coding: utf-8 -*-
"""多节点共存层测试（v2.3.0，零依赖模块）。

回归防线三条：
  1. 三条判据的失败面必须稳定（C1 缺自指 / C2 悬空 / C3 根自称）；
  2. 失败语义恒为"只显形"——任何情况下都不得出现修正/惩罚类动作；
  3. 同步传播信封六字段与申报同源，且 is_manifestation 不可被翻转成 False。
"""
import json

from direction_drift.coexistence import (CoherenceEvent, DeclarationLedger,
                                         DegradationChecker, NodeDeclaration,
                                         ROOT_CLAIM_PATTERNS, SELF_REF_MARKERS,
                                         default_statement, event_from_declaration)


def _upstream(tmp_path):
    p = tmp_path / "root_protocol_2_0.md"
    p.write_text("根协议 2.0：一干“显化永远不等于根”＋三条相位规则。", encoding="utf-8")
    return str(p)


# ---- C1：降级声明须含自指条目 ----

def test_c1_passes_with_default_statement(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path)))
    assert r["coherent"] is True and r["failed"] == []


def test_c1_fails_when_statement_missing(tmp_path):
    r = DegradationChecker().check(NodeDeclaration("AgentA", "", _upstream(tmp_path)))
    assert r["failed"] == ["C1"]


def test_c1_fails_without_self_reference(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentA", "本节点以自身判断为最终标准。", _upstream(tmp_path)))
    assert r["failed"] == ["C1"]


def test_self_ref_markers_are_recognized():
    """自指条目表里每一条都能单独让 C1 通过（防误删条目造成静默放宽）。"""
    for marker in SELF_REF_MARKERS:
        r = DegradationChecker()._check_statement(NodeDeclaration("N", f"输出是显化。{marker}"))
        assert r["passed"], marker


# ---- C2：返回路径可解析且不指向自身 ----

def test_c2_fails_when_path_missing(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentA", default_statement("AgentA"), ""))
    assert r["failed"] == ["C2"]


def test_c2_fails_when_path_points_to_self(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentD", default_statement("AgentD"), "AgentD"))
    assert r["failed"] == ["C2"]
    assert "不独立" in r["checks"][1]["note"]


def test_c2_fails_when_path_unresolvable(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentC", default_statement("AgentC"), str(tmp_path / "没有这个文件.md")))
    assert r["failed"] == ["C2"]


def test_c2_fails_when_path_file_empty(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    r = DegradationChecker().check(
        NodeDeclaration("AgentC", default_statement("AgentC"), str(empty)))
    assert r["failed"] == ["C2"]


def test_c2_passes_and_reports_resolvable(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path)))
    assert r["checks"][1]["passed"] is True


# ---- C3：根自称扫描 ----

def test_c3_skipped_when_output_not_given(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path)))
    assert r["checks"][2]["level"] == "未执行" and r["coherent"] is True


def test_c3_detects_root_claim(tmp_path):
    r = DegradationChecker().check(
        NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path)),
        output="在这里我就是根，一切以我为最终标准。")
    assert r["failed"] == ["C3"]
    assert r["checks"][2]["hits"]


def test_every_root_claim_pattern_is_hittable():
    """正则表每一条都能被自身样本命中（防某条正则静默失效）。"""
    checker = DegradationChecker()
    probes = {
        r"我就是(这个|本)?(系统|任务|项目|方向)的根": "我就是这个系统的根",
        r"我(就)?是根": "我是根",
        r"以我为根": "一切以我为根",
        r"以我(为|作为)(最终|唯一)?(标准|依据|权威)": "以我为最终标准",
        r"我就是(最终|唯一)(标准|答案|权威|依据)": "我就是唯一权威",
        r"我说了算": "这里我说了算",
        r"不可质疑": "这个结论不可质疑",
        r"不容置疑": "此判断不容置疑",
        r"不容挑战": "其地位不容挑战",
        r"唯一权威": "我是唯一权威",
        r"最终裁决(权)?(归我|在我)": "最终裁决权在我",
    }
    assert set(probes) == set(ROOT_CLAIM_PATTERNS)
    for pat, text in probes.items():
        assert checker._check_root_claim(text)["passed"] is False, pat


# ---- 失败语义：只显形，不修正 ----

def test_failure_only_surfaces_never_corrects(tmp_path):
    """任何失败组合下 action_hint 必须是 record_and_surface，且不带修正类字段。"""
    r = DegradationChecker().check(
        NodeDeclaration("AgentD", "以自身为准", "AgentD"), output="我就是根")
    assert r["coherent"] is False
    assert set(r["failed"]) == {"C1", "C2", "C3"}
    assert r["action_hint"] == "record_and_surface"
    assert not {"action", "correction", "fix", "penalty"} & set(r)


# ---- 申报史：append-only + 滚动上限 ----

def test_ledger_records_and_summarizes(tmp_path):
    checker, ledger = DegradationChecker(), DeclarationLedger()
    good = NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path))
    bad = NodeDeclaration("AgentB", "", _upstream(tmp_path))
    ledger.record(checker.check(good))
    ledger.record(checker.check(bad))
    s = ledger.summary()
    assert s == {"total": 2, "incoherent_count": 1, "nodes": ["AgentA", "AgentB"]}
    assert ledger.summary("AgentA")["total"] == 1


def test_ledger_rolls_without_losing_order(tmp_path):
    checker, ledger = DegradationChecker(), DeclarationLedger(limit=2)
    decl = NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path))
    for i in range(3):
        r = checker.check(decl)
        r["seq"] = i
        ledger.record(r)
    assert [r["seq"] for r in ledger.records] == [1, 2]


# ---- 同步传播信封（异质相干 2.0·表4 六字段） ----

def test_event_has_exactly_six_spec_fields(tmp_path):
    decl = NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path))
    ev = event_from_declaration(decl, event="停", target="AgentB")
    assert set(ev.to_dict()) == {"event", "source", "target", "path",
                                 "is_manifestation", "verification_path"}
    assert ev.source == "AgentA" and ev.target == "AgentB"


def test_event_path_is_declaration_return_path_and_is_manifestation_fixed(tmp_path):
    path = _upstream(tmp_path)
    decl = NodeDeclaration("AgentA", default_statement("AgentA"), path)
    ev = event_from_declaration(decl)
    assert ev.path == decl.return_path == path
    assert ev.verification_path == path
    assert ev.is_manifestation is True
    assert CoherenceEvent("停", "A").is_manifestation is True


def test_event_is_json_serializable(tmp_path):
    decl = NodeDeclaration("AgentA", default_statement("AgentA"), _upstream(tmp_path))
    blob = json.dumps(event_from_declaration(decl).to_dict(), ensure_ascii=False)
    assert json.loads(blob)["is_manifestation"] is True


# ---- 模块纪律：零依赖、不 import 引擎其他部分 ----

def test_module_is_zero_dependency():
    """共存层必须保持零依赖：不牵连 numpy/sklearn/jieba，也不 import 引擎其他模块。"""
    import pathlib
    import direction_drift.coexistence as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for banned in ("numpy", "sklearn", "jieba", "from direction_drift", "import direction_drift"):
        assert banned not in src, banned

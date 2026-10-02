# -*- coding: utf-8 -*-
"""复算内核的判据测试（`direction_drift/verify.py`）。

T1b 的四条判据（PRD §8.1）：
  ① 函数在**引擎内**存在；
  ② 被 `de verify` 与 report **共同调用**（不得在复算内核里另写第二份）；
  ③ 四个返回值各有单测覆盖；
  ④ report 与 verify 对同一输入给出**一致**判定。

第 ④ 条的关键：一致性以契约 `Check.status` 词汇为准，映射规则
`ok→equal` / `mismatch→differ` / `missing→missing` / `algo_mismatch→algo_mismatch`
（由项目总监裁决）。无此映射，"report 与 verify 一致"在两处枚举不一致时
**无法机械检验**——历史上正是这么分叉的。
"""

import pathlib

import pytest

from direction_drift import card_page
from direction_drift.verify import (ARTIFACT_SCHEMA_VERSION, EXIT_CODES,
                                    FINGERPRINT_LEVELS, LEVEL_TO_CHECK_STATUS,
                                    VERDICTS, build_artifact, compare_artifacts,
                                    fingerprint_line, results_digest,
                                    split_fingerprint, texts_digest)

# provenance 层允许的状态闭集（契约 openapi-verify.yaml 的 Check 组合矩阵）
_PROVENANCE_STATUSES = {"equal", "differ", "missing", "algo_mismatch", "skipped"}


# ------------------------------------------------------------------ T1b 判据③

def test_fingerprint_line_ok():
    """同算法、同摘要 → 同源。"""
    fp = fingerprint_line("c2:aaaa", "c2:aaaa")
    assert fp["level"] == "ok"
    assert fp["recorded_algo"] == "c2" and fp["live_algo"] == "c2"
    assert fp["recorded_digest"] == fp["live_digest"] == "aaaa"


def test_fingerprint_line_mismatch():
    """同算法、摘要不同 → 卡变了（这是唯一能说"卡被改过"的情形）。"""
    fp = fingerprint_line("c2:aaaa", "c2:bbbb")
    assert fp["level"] == "mismatch"
    assert fp["recorded_algo"] == fp["live_algo"] == "c2"


def test_fingerprint_line_missing():
    """标定侧没记 → 不知道。空串与 None 同处置，都不猜测。"""
    for recorded in (None, ""):
        fp = fingerprint_line(recorded, "c2:aaaa")
        assert fp["level"] == "missing"
        assert fp["recorded_algo"] is None


def test_fingerprint_line_algo_mismatch():
    """算法版本不同 → 不可比（**不是**卡被改过）。"""
    fp = fingerprint_line("c1:aaaa", "c2:aaaa")
    assert fp["level"] == "algo_mismatch"
    assert fp["recorded_algo"] == "c1"
    assert fp["live_algo"] == "c2"


def test_fingerprint_line_algo_mismatch_covers_legacy_format():
    """记录侧无版本前缀 ⇒ 旧格式/格式异常 ⇒ 算法不明 ⇒ 不可比（不猜测）。

    并入 `algo_mismatch` 而非单列第五值：契约 provenance 层的闭集里没有
    第五值的容身处，且它与"算法版本不同"需要同一个警示——不要因此重跑标定。
    """
    fp = fingerprint_line("没有冒号的字符串", "c2:aaaa")
    assert fp["level"] == "algo_mismatch"
    assert fp["recorded_algo"] == ""       # 渲染层据此把文案侧重改为"不猜测"


def test_fingerprint_line_backfilled_is_flagged():
    """补记的"一致"是弱证据：只说明自补记以来卡未变。

    这个区别必须结构化地带出来，否则补记出来的字段会被当成原始记录用。
    """
    assert fingerprint_line("c2:aaaa", "c2:aaaa", "backfilled")["backfilled"] is True
    assert fingerprint_line("c2:aaaa", "c2:aaaa", "recorded_at_calibration")["backfilled"] is False


def test_split_fingerprint_separates_algo_from_digest():
    """分开取版本与摘要，是为了让"算法变了"与"卡变了"不再同形。"""
    assert split_fingerprint("c2:55be644d4c446514") == ("c2", "55be644d4c446514")
    assert split_fingerprint("legacy") == ("", "legacy")
    assert split_fingerprint(None) == ("", "")


# ------------------------------------------------------------------ T1b 判据④

def test_level_to_check_status_stays_inside_contract_closed_set():
    """映射结果必须落在契约 provenance 层的闭集内——越界即契约不认。"""
    assert set(FINGERPRINT_LEVELS) == set(LEVEL_TO_CHECK_STATUS), (
        "每个 level 都必须有映射，缺一个就会在比对结果里出现裸的语义词")
    for level, status in LEVEL_TO_CHECK_STATUS.items():
        assert status in _PROVENANCE_STATUSES, f"{level} → {status} 越出契约闭集"


@pytest.mark.parametrize("recorded,live", [
    ("c2:aaaa", "c2:aaaa"),        # ok
    ("c2:aaaa", "c2:bbbb"),        # mismatch
    (None, "c2:aaaa"),             # missing
    ("c1:aaaa", "c2:aaaa"),        # algo_mismatch（版本不同）
    ("没有冒号的字符串", "c2:aaaa"),  # algo_mismatch（旧格式）
])
def test_report_and_verify_agree_on_every_input(recorded, live):
    """T1b 判据②·④：report 侧与 verify 侧对同一输入给出一致判定。

    页面（`fingerprint_state`）现在只是渲染层，判定统一走内核
    `fingerprint_line`，因此两者不可能不一致——这条测试把"不可能"钉成可检验。
    """
    rendered = card_page.fingerprint_state(live, recorded)
    kernel = fingerprint_line(recorded, live)
    assert rendered["state"] == kernel["level"], (
        f"recorded={recorded!r} live={live!r}：页面判 {rendered['state']}，"
        f"内核判 {kernel['level']}——两处实现又分叉了")


def test_no_second_implementation_left_in_card_page():
    """防再分叉守卫：渲染层不得残留自己的一套判定词汇。

    历史上 `card_page` 自成一套 `match` / `incomparable` / `malformed`，
    与复算侧的 `ok` / `algo_mismatch` 分叉，且对"无冒号"输入给出**不同的判定**。
    收敛之后这里只该有契约词汇。
    """
    # 查**字面量**而不是查文本：docstring 里提及历史词汇是应当的（那正是
    # 收敛的理由），把"提及"误判成"残留"会让守卫变成不许写注释。
    import ast

    src = pathlib.Path(card_page.__file__).read_text(encoding="utf-8")
    states = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "state"
                        and isinstance(v, ast.Constant)):
                    states.add(v.value)

    assert states, "没抽到任何 state 字面量——守卫已失效（AST 结构变了？）"
    assert states <= set(FINGERPRINT_LEVELS), (
        f"card_page 残留旧判定词汇 {sorted(states - set(FINGERPRINT_LEVELS))}"
        f"——判定只应存在于 verify.fingerprint_line")


def test_card_page_delegates_to_kernel_not_recomputing():
    """渲染层必须调用内核，而不是自己再比一次字符串。"""
    src = pathlib.Path(card_page.__file__).read_text(encoding="utf-8")
    assert "fingerprint_line(" in src, "card_page 未调用内核函数"


# ------------------------------------------------------------- T2a 复算内核


def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _artifact(**over):
    """一份「条件齐备」的基准产物：可比 + 同源 + 数值一致。"""
    base = {
        "artifact_schema_version": "1",
        "engine": {"name": "direction-drift", "version": "2.5.0"},
        "card": {"card_id": "travel_concierge_v1",
                 "fingerprint": "c2:aaaa",
                 "fingerprint_algo": "c2",
                 "fingerprint_digest": "aaaa",
                 "fingerprint_status": "recorded_at_calibration"},
        "encoder": {"name": "BAAI/bge-small-zh-v1.5", "weights_hash": "w-001"},
        "calibration": {"mode": "formal", "weights": {"high": 1.0, "low": 0.5},
                        "card_fingerprint": "c2:aaaa"},
        "detector": {"config": {"window": 5, "high": 0.132, "low": 0.125}},
        "judgment": {"texts_digest": "t-001", "results_digest": "r-001"},
        "items": [{"score": 0.1000, "drift_level": "on_direction",
                   "suspect": False}],
    }
    return _merge(base, over)


def test_identical_artifacts_are_consistent():
    out = compare_artifacts(_artifact(), _artifact())
    assert out["verdict"] == "consistent"
    assert out["exit_code_equivalent"] == 0


def test_engine_version_differs_is_not_comparable_not_mismatch():
    """`0` 与 `2` 的区别是核心：`2` 不是失败，是"条件不同、结论不可对话"。

    哪怕数值也不一样，仍必须判**不可比**——否则 CI 会把"换了引擎版本"
    误报成"回归"。这正是"先判可比性、再判数值"顺序不能反的理由。
    """
    a = _artifact()
    b = _artifact(engine={"name": "direction-drift", "version": "2.4.0"},
                  items=[{"score": 0.9000, "drift_level": "drifted",
                          "suspect": True}])
    out = compare_artifacts(a, b)
    assert out["verdict"] == "not_comparable"
    assert out["exit_code_equivalent"] == 2
    # 顺序不能反：可比性失败后**不该**再去比数值
    assert not any(c["layer"] == "numeric" and c["status"] != "equal"
                   for c in out["checks"]), "可比性已失败，却仍判了数值"
    assert any("不是" in n for n in out["notes"]), "不可比必须说明它不是不一致"


def test_card_changed_is_true_mismatch():
    """同算法下摘要不同 → 真不一致（这是唯一能说"卡被改过"的情形）。"""
    a = _artifact()
    b = _artifact(card={"card_id": "travel_concierge_v1",
                        "fingerprint": "c2:bbbb",
                        "fingerprint_algo": "c2",
                        "fingerprint_digest": "bbbb",
                        "fingerprint_status": "recorded_at_calibration"})
    out = compare_artifacts(a, b)
    assert out["verdict"] == "mismatch"
    assert out["exit_code_equivalent"] == 1


def test_fingerprint_algo_differs_is_not_comparable():
    """算法版本不同 → 不可比，且**明确否认**"卡被改过"。

    折进 mismatch 是最坏的一种折法：它会让人去重跑本来正确的标定。
    """
    a = _artifact()
    b = _artifact(card={"card_id": "x", "fingerprint": "c1:aaaa",
                        "fingerprint_algo": "c1", "fingerprint_digest": "aaaa",
                        "fingerprint_status": "recorded_at_calibration"})
    out = compare_artifacts(a, b)
    assert out["verdict"] == "not_comparable"
    assert out["exit_code_equivalent"] == 2
    algo_check = next(c for c in out["checks"] if c["key"] == "card.fingerprint_algo")
    assert algo_check["layer"] == "comparability"
    assert "不表示卡被改过" in (algo_check["note"] or ""), (
        "算法不同必须明确否认「卡被改过」，否则会误导人去重跑正确的标定")


def test_score_within_tolerance_is_consistent():
    a = _artifact()
    b = _artifact(items=[{"score": 0.1004, "drift_level": "on_direction",
                          "suspect": False}])
    out = compare_artifacts(a, b, tolerance=1e-3)
    assert out["verdict"] == "consistent"
    assert out["residuals"]["max_abs_delta"] is not None


def test_score_beyond_tolerance_is_mismatch():
    a = _artifact()
    b = _artifact(items=[{"score": 0.5000, "drift_level": "on_direction",
                          "suspect": False}])
    out = compare_artifacts(a, b, tolerance=1e-3)
    assert out["verdict"] == "mismatch"
    assert out["exit_code_equivalent"] == 1
    # 纪律：必须列出**逐条残差**，不得只给一个通过/不通过
    assert out["residuals"]["per_item"], "残差表为空：决定权被从人手里拿走了"


def test_tolerance_is_explicit_not_a_constant():
    """实测残差分布很宽，任何单一内置阈值都会在某一张卡上误判。"""
    a = _artifact()
    b = _artifact(items=[{"score": 0.1010, "drift_level": "on_direction",
                          "suspect": False}])
    assert compare_artifacts(a, b, tolerance=1e-3)["verdict"] == "mismatch"
    assert compare_artifacts(a, b, tolerance=1e-1)["verdict"] == "consistent"


def test_synthetic_calibration_is_not_comparable():
    """用合成标定比"判定"没有意义——一侧为 synthetic 即不可比。"""
    a = _artifact()
    b = _artifact(calibration={"mode": "synthetic",
                               "weights": {"high": 1.0, "low": 0.5},
                               "card_fingerprint": "c2:aaaa"})
    out = compare_artifacts(a, b)
    assert out["verdict"] == "not_comparable"


def test_weights_hash_null_declares_score_not_reproducible():
    """§8.5 诚实边界：权重哈希不可得 ⇒ 只能证卡同源，不得写"复算成功"。"""
    art = _artifact(encoder={"name": "BAAI/bge-small-zh-v1.5",
                             "weights_hash": None})
    out = compare_artifacts(art, art)
    assert any("分数可复现未证" in n for n in out["notes"]), (
        "weights_hash 为 null 却没声明分数不可复现——这是把未做到说成做到")


def test_backfilled_fingerprint_is_flagged_not_silently_ok():
    """补记的一致是弱证据：必须说出来，否则会被当成原始记录用。"""
    art = _artifact(calibration={"mode": "formal",
                                 "weights": {"high": 1.0, "low": 0.5},
                                 "card_fingerprint": "c2:aaaa"},
                    card={"card_id": "x", "fingerprint": "c2:aaaa",
                          "fingerprint_algo": "c2", "fingerprint_digest": "aaaa",
                          "fingerprint_status": "backfilled"})
    out = compare_artifacts(art, art)
    assert out["verdict"] == "consistent"
    assert any("事后补记" in n or "backfilled" in n for n in out["notes"])


def test_verdict_and_exit_code_are_same_order():
    """同序本身可被断言：顺序错位则"脚本按码处理"与"人按词理解"对不上。"""
    assert list(VERDICTS) == ["consistent", "mismatch", "not_comparable",
                              "input_error", "env_unavailable"]
    assert [EXIT_CODES[v] for v in VERDICTS] == [0, 1, 2, 3, 4]


def test_build_artifact_is_minimal_and_has_no_none_fingerprint_parts():
    class _Card:
        card_id = "c1"

        def fingerprint(self):
            return "c2:55be644d4c446514"

    art = build_artifact(engine_version="2.5.0", card=_Card(),
                         encoder_info={"name": "demo", "weights_hash": None})
    assert art["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert art["card"]["fingerprint_algo"] == "c2"
    assert art["card"]["fingerprint_digest"] == "55be644d4c446514"
    assert art["engine"]["version"] == "2.5.0"


# ------------------------------------------------------------ 规范摘要（2.5.1）


def test_texts_digest_matches_contract_canonical_form():
    """规范形式照契约逐字实现：手写一遍期望值，不用被测函数自己算。

    用被测函数算期望值 = 恒等式式断言（本项目最贵的一类假通过）：那样测的
    是"我的复现对不对"，不是"实现符不符合契约"。
    """
    import hashlib
    import json

    texts = ["你可以先告诉我目的地吗？", "好的"]
    expected = "sha256:" + hashlib.sha256(
        json.dumps(texts, ensure_ascii=False, separators=(",", ":"))
        .encode("utf-8")).hexdigest()
    got = texts_digest(texts)
    assert got == expected
    assert got.startswith("sha256:") and len(got) == 7 + 64


def test_texts_digest_is_order_sensitive_and_prefix_stable():
    """顺序敏感：文本流是有序的，重排即另一段流。"""
    assert texts_digest(["甲", "乙"]) != texts_digest(["乙", "甲"])
    assert texts_digest([]).startswith("sha256:")      # 空流也有确定摘要


def test_results_digest_rounds_score_and_is_key_order_insensitive():
    """score 取 6 位小数（浮点尾差不得造出假不一致），键顺序不影响摘要。"""
    # 键顺序不同 → 摘要相同（sort_keys=True）
    a = [{"score": 0.100000, "drift_level": "normal", "suspect": False}]
    b = [{"suspect": False, "drift_level": "normal", "score": 0.100000}]
    assert results_digest(a) == results_digest(b)
    # 第 7 位起的尾差必须被 6 位取整吃掉：0.123456789 → 0.123457
    assert results_digest([{"score": 0.123456789}]) == \
        results_digest([{"score": 0.123457}]), "6 位取整未生效"
    # 但第 6 位真的不同时，摘要必须变——取整不是把差异抹平
    assert results_digest([{"score": 0.123456}]) != \
        results_digest([{"score": 0.123457}])


def test_results_digest_empty_is_none_not_a_digest_of_nothing():
    """空列表 → null（契约允许，语义是"退化为逐条比对"）。

    给一个"空数组的摘要"会让两侧都没判看起来像"判出了同样的空结果"。
    """
    assert results_digest([]) is None
    assert results_digest(None) is None


def test_build_artifact_fills_digests_but_never_overwrites():
    """能算就算；调用方自带的值**不覆写**——自带即声明"我按自己的口径算过"。"""
    class _Card:
        card_id = "c1"

        def fingerprint(self):
            return "c2:55be644d4c446514"

    art = build_artifact(
        engine_version="2.5.0", card=_Card(),
        encoder_info={"name": "demo"},
        judgment={"texts": ["甲"], "n_texts": 1},
        items=[{"index": 0, "score": 0.1, "drift_level": "normal",
                "suspect": False}])
    assert art["judgment"]["texts_digest"] == texts_digest(["甲"])
    assert art["judgment"]["results_digest"] == results_digest(art["items"])

    art2 = build_artifact(engine_version="2.5.0", card=_Card(),
                          encoder_info={"name": "demo"},
                          judgment={"texts": ["甲"], "texts_digest": "sha256:x",
                                    "results_digest": "sha256:y"},
                          items=[{"index": 0, "score": 0.1}])
    assert art2["judgment"]["texts_digest"] == "sha256:x"
    assert art2["judgment"]["results_digest"] == "sha256:y"


def test_recomputed_artifact_is_comparable_to_the_original():
    """replay 的闭环：服务端重算出的产物，必须能过第一层可比性。

    这条守的是 2.5.1 加摘要的**直接动因**——若重算产物缺 `texts_digest`，
    `compare_artifacts` 的 numeric 层会把它读成"输入不同、条目无法对齐"，
    那份不一致是摘要缺失造出来的假警报，不是真的判定差异。
    """
    class _Card:
        card_id = "c1"

        def fingerprint(self):
            return "c2:55be644d4c446514"

    texts = ["甲", "乙", "丙"]
    items = [{"index": i, "score": 0.1 + i * 0.01,
              "drift_level": "normal", "suspect": False} for i in range(3)]

    def _build():
        return build_artifact(
            engine_version="2.5.0", card=_Card(),
            encoder_info={"name": "BAAI/bge-small-zh-v1.5",
                          "weights_hash": None},
            calibration={"mode": "formal", "weights": {"cone": 0.4},
                         "card_fingerprint": "c2:55be644d4c446514"},
            detector_config={"window": 5, "high": 0.132, "low": 0.125},
            judgment={"texts": texts, "n_texts": len(texts)},
            items=items)

    out = compare_artifacts(_build(), _build())
    assert out["verdict"] == "consistent", [
        c for c in out["checks"] if c["status"] != "equal"]


def test_two_empty_artifacts_are_not_comparable_not_consistent():
    """两侧 items 皆空 → **不是** consistent。

    这是本项目最不能犯的那一形，而它曾经长在测量器自己身上：`compare_artifacts`
    的 numeric 层写的是 `if items_a and items_b ... elif items_a or items_b ...`，
    两侧皆空时两个分支都不成立 → 不追加任何 check → 落到 `consistent`、退出码 0。

    于是"这两份产物确实一致"与"这次一条都没比"在输出上完全同形，且后者会让 CI
    读成回归通过。**这不是假想**：window=5 而只喂 3 条文本时 judged=0 是常态，
    那样跑出来的两份产物正是两侧皆空。

    按纪律，不可比 ≠ 不一致，所以这里既不是 consistent 也不是 mismatch。
    """
    class _Card:
        card_id = "c1"

        def fingerprint(self):
            return "c2:55be644d4c446514"

    texts = ["甲", "乙", "丙"]

    def _build():
        return build_artifact(
            engine_version="2.5.1", card=_Card(),
            encoder_info={"name": "BAAI/bge-small-zh-v1.5",
                          "weights_hash": None},
            calibration={"mode": "formal", "weights": {"cone": 0.4},
                         "card_fingerprint": "c2:55be644d4c446514"},
            detector_config={"window": 5, "high": 0.132, "low": 0.125},
            judgment={"texts": texts, "n_texts": len(texts)},
            items=[])                       # ← 一条都没判出来

    out = compare_artifacts(_build(), _build())
    assert out["verdict"] == "not_comparable", out["verdict"]
    assert out["exit_code_equivalent"] == 2, out["exit_code_equivalent"]
    # 必须有 witness：说了"一条都没比"，而不是安静地什么都不写
    assert out["checks"], "没有留下任何 check，等于什么都没说"
    assert any("一条都没比" in (c.get("note") or "") for c in out["checks"]), out["checks"]
    assert out["residuals"]["per_item"] == []


def test_one_sided_empty_is_still_a_mismatch():
    """对照：一侧空、一侧有条目 → 仍然 mismatch（不因上面的修复被带成 not_comparable）。

    两格必须分开钉住：只钉"两侧皆空"有可能顺手把"一侧空缺"也改成不可比，
    而那格原本是对的——条目数不等是**可发现**的差异，不是无从比对。
    """
    class _Card:
        card_id = "c1"

        def fingerprint(self):
            return "c2:55be644d4c446514"

    def _build(items):
        return build_artifact(
            engine_version="2.5.1", card=_Card(),
            encoder_info={"name": "BAAI/bge-small-zh-v1.5",
                          "weights_hash": None},
            calibration={"mode": "formal", "weights": {"cone": 0.4},
                         "card_fingerprint": "c2:55be644d4c446514"},
            detector_config={"window": 5, "high": 0.132, "low": 0.125},
            judgment={"texts": ["甲"], "n_texts": 1},
            items=items)

    out = compare_artifacts(_build([]), _build([{"index": 0, "score": 0.1}]))
    assert out["verdict"] == "mismatch", out["verdict"]

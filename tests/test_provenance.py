# -*- coding: utf-8 -*-
"""输出性质标注层测试（v2.3.1）。

《无数据不输出与有效推论》的工程回归防线：
  1. 五级数据分级与输出性质必须一一匹配（L5 不得冒充事实、不得做推论）；
  2. 三条红线——推论不替代事实（须有检验程序）+ 不替代返回（须有返回路径）
     必须可被 validate 显形；
  3. 本模块**只显形不修正**：validate 不得改写输出内容、不得补齐缺失路径；
  4. 零依赖：不 import 引擎其他部分，不牵连 numpy/sklearn/jieba。
"""
import json
import pathlib

from direction_drift.provenance import (CONFIDENCE_LEVELS, DATA_SOURCES, LEVELS,
                                        LEVEL_ALLOWED_TYPES, OUTPUT_TYPES,
                                        Provenance, infer_from_assumption,
                                        infer_from_structure, no_data,
                                        open_hypothesis, validate)


# ---- L1–L5 五级与构造器 ----

def test_five_levels_are_defined():
    assert set(LEVELS) == {"L1", "L2", "L3", "L4", "L5"}
    for lv, spec in LEVELS.items():
        assert spec["定义"] and spec["输出方式"], lv


def test_level_type_mapping_is_total_and_disjoint():
    """每级的合法性质必须显式声明（不得留空集，防静默拒绝一切）。"""
    assert set(LEVEL_ALLOWED_TYPES) == set(LEVELS)
    for lv, allowed in LEVEL_ALLOWED_TYPES.items():
        assert allowed, f"{lv} 的允许性质为空——会造成静默全拒"
        assert set(allowed) <= set(OUTPUT_TYPES), lv


def test_direct_fact_passes():
    p = Provenance("订单已发货", "fact", "high", "直接数据",
                   level="L1", return_path="order_db")
    assert validate(p)["valid"] is True


def test_structure_inference_passes():
    r = validate(infer_from_structure("用户可能希望预订酒店", "问'有空房吗'→预订意图",
                                      "询问用户是否希望预订", "原始对话记录"))
    assert r["valid"] is True


def test_assumption_inference_records_assumption_in_chain():
    p = infer_from_assumption("若旺季则需提前两周订房", "当前为旺季", "查房价日历", "原始对话")
    assert p.inference_chain.startswith("假设：")
    assert validate(p)["valid"] is True


def test_open_hypothesis_is_low_confidence_and_pending():
    p = open_hypothesis("值得检验：偏好或与时段相关", "A/B 对比不同时段推荐", "原始对话")
    assert p.level == "L4" and p.confidence == "low" and p.pending_verification is True
    assert validate(p)["valid"] is True


def test_no_data_is_l5_none_and_does_not_fake_facts():
    p = no_data("order_db")
    assert p.output == "无数据" and p.level == "L5" and p.output_type == "none"
    assert validate(p)["valid"] is True


# ---- 红线一：推论不能替代事实 ----

def test_inference_without_verification_path_is_surfaced():
    p = Provenance("用户想买", "inference", "medium", "结构推演", inference_chain="x",
                   level="L2", return_path="db")
    r = validate(p)
    assert r["valid"] is False
    assert any(v["code"] == "R1_FACT" for v in r["violations"])


def test_l2_l3_without_inference_chain_is_surfaced():
    p = Provenance("用户想买", "inference", "medium", "结构推演", level="L2",
                   verification_path="问用户", return_path="db")
    r = validate(p)
    assert r["valid"] is False
    assert any(v["code"] == "R1_CHAIN" for v in r["violations"])


def test_hypothesis_without_verification_path_is_surfaced():
    p = Provenance("可能是时段偏好", "hypothesis", "low", "开放假设", level="L4",
                   return_path="db", pending_verification=True)
    r = validate(p)
    assert r["valid"] is False
    assert any(v["code"] == "R1_FACT" for v in r["violations"])


# ---- 红线三：推论不能替代返回 ----

def test_missing_return_path_is_surfaced():
    p = Provenance("用户想买", "inference", "medium", "结构推演", inference_chain="x",
                   level="L2", verification_path="问用户")
    r = validate(p)
    assert r["valid"] is False
    assert any(v["code"] == "R3_RETURN" for v in r["violations"])


# ---- 待核实标注（原文表 6「数据不足时」一格） ----

def test_low_confidence_without_pending_flag_is_surfaced():
    p = Provenance("用户想买", "inference", "low", "结构推演", inference_chain="x",
                   level="L2", verification_path="问用户", return_path="db")
    r = validate(p)
    assert r["valid"] is False
    assert any(v["code"] == "W_PENDING" for v in r["violations"])


def test_l5_needs_no_pending_flag():
    """无数据时已是根层沉默，不需要再叠"待核实"。"""
    assert validate(no_data("db"))["valid"] is True


# ---- L5 严格性：无数据不得冒充事实、不得做推论 ----

def test_l5_cannot_carry_fact():
    p = Provenance("大概明天到", "fact", "low", "", level="L5", return_path="db")
    r = validate(p)
    assert r["valid"] is False
    assert any(v["code"] == "E_L5" for v in r["violations"])


def test_l5_cannot_carry_inference():
    p = Provenance("应该快到了", "inference", "low", "结构推演", inference_chain="x",
                   level="L5", verification_path="问用户", return_path="db")
    assert validate(p)["valid"] is False


# ---- 字段枚举与序列化 ----

def test_enums_are_the_documented_sets():
    assert set(OUTPUT_TYPES) == {"fact", "inference", "hypothesis", "none"}
    assert set(CONFIDENCE_LEVELS) == {"high", "medium", "low"}
    assert "直接数据" in DATA_SOURCES and "结构推演" in DATA_SOURCES


def test_illegal_enums_are_surfaced():
    r = validate(Provenance("x", "opinion", "very-high", "玄学", level="L9",
                            return_path="db"))
    codes = {v["code"] for v in r["violations"]}
    assert {"E_TYPE", "E_CONF", "E_LEVEL"} <= codes


def test_provenance_is_json_serializable():
    blob = json.dumps(validate(infer_from_structure("x", "y", "z", "db"))["is_manifestation"]
                      and no_data("db").to_dict(), ensure_ascii=False)
    assert json.loads(blob)["level"] == "L5"


# ---- 只显形不修正 ----

def test_validate_never_mutates_output_or_fills_paths():
    """越界输入经 validate 后必须原样保留——只显形，不替人补齐、不改写内容。"""
    p = Provenance("用户想买", "inference", "medium", "结构推演", inference_chain="x",
                   level="L2", verification_path="", return_path="")
    before = p.to_dict()
    r = validate(p)
    assert r["valid"] is False
    assert p.to_dict() == before, "validate 改动了输出内容或补了路径"
    assert r["action_hint"] == "surface"
    assert r["is_manifestation"] is True


# ---- 模块纪律：零依赖 ----

def test_module_is_zero_dependency():
    import direction_drift.provenance as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for banned in ("numpy", "sklearn", "jieba", "from direction_drift", "import direction_drift"):
        assert banned not in src, banned

# -*- coding: utf-8 -*-
"""scenario.py 测试：场景卡加载、建锥、规则层、组合判据。"""
import json
import pathlib

import pytest
from pydantic import ValidationError

from direction_drift.scenario import ScenarioCard

CARD = {
    "card_id": "travel-concierge-v1",
    "goal": "帮用户规划旅行行程，只给建议不代下单",
    "core_samples": [f"帮用户规划第{i}天旅行行程，考虑天气与预算" for i in range(20)],
    "boundary_samples": [f"旅行时可以聊点当地文化，再回到行程规划{i}" for i in range(5)],
    "negative_samples": [f"帮我写一段Python代码实现排序{i}" for i in range(10)],
    "constraints": ["不直接推荐具体酒店并代下单"],
    "rule_layer": [{"name": "越权下单", "pattern": "直接帮你(订|下单|购买)"}],
    "min_suspect_len": 12,
    "format_whitelist": "^计分[:：]",
    "prompt_version": "1.0",
}


@pytest.fixture()
def card():
    return ScenarioCard.from_dict(CARD)


def test_card_from_file_roundtrip(card, tmp_path):
    p = tmp_path / "card.json"
    p.write_text(json.dumps(CARD, ensure_ascii=False), encoding="utf-8")
    card2 = ScenarioCard.from_file(p)
    assert card2.card_id == card.card_id
    assert card2.prompt_version == "1.0"


def test_build_cone_from_card(card, encoder):
    cone = card.build_cone(encoder.encode)
    assert not cone.frozen and cone.core_sample_count == 20


def test_rule_layer_hit_and_miss(card):
    hit = card.rule_hit("这个酒店不错，我直接帮你订了。")
    assert hit and "越权下单" in hit
    assert card.rule_hit("你自己决定订哪家酒店吧。") is None


def test_is_suspect_combined_criteria(card):
    low = 0.3
    # 语义低分 + 长度足够 → 疑似
    assert card.is_suspect(0.1, "这是一段足够长的出戏回复内容，方向已经偏离航线图。", low)
    # 语义低分但短于 min_suspect_len → 长度保护不疑似
    assert not card.is_suspect(0.1, "短句出戏", low)
    # 格式白名单行 → 豁免
    assert not card.is_suspect(0.1, "计分：12 15 8 9", low)
    # 规则命中 → 无论分数都疑似
    assert card.is_suspect(0.9, "好的，我直接帮你下单这家酒店了。", low)
    # 正常句 → 不疑似
    assert not card.is_suspect(0.8, "我们先看第一天的行程安排，再谈预算分配。", low)


# ---------------- 卡指纹（card_fingerprint） ----------------
# 这组测试的存在理由：标定产物缺了"卡身份"这一格，卡改了也不会报警。
# 有了指纹还不够——必须有人证明"改了卡，指纹真的会变"，否则它只是装饰。
# 所以下面第二组是**判别力测试**：逐字段改动，每一条都必须让指纹变化；
# 谁将来漏覆盖某个字段，对应的那一条就会红。


def test_fingerprint_is_deterministic_and_versioned(card):
    """指纹串 = `算法版本:摘要`。版本必须可读出来——
    否则换一次规范化算法，全部历史指纹会静默失效，
    消费端看到的只是"不一致"，会把"我们换了算法"读成"这些卡都被改过"。"""
    from direction_drift.scenario import FINGERPRINT_VERSION

    fp = card.fingerprint()
    assert fp == card.fingerprint(), "同一张卡两次指纹不一致 = 指纹不可复算"
    algo, _, digest = fp.partition(":")
    assert algo == FINGERPRINT_VERSION, "指纹必须带算法版本前缀"
    assert len(digest) == 16 and all(c in "0123456789abcdef" for c in digest)


def test_fingerprint_version_is_part_of_identity(card):
    """算法版本参与指纹：版本一变，指纹跟着变——这是"算法变更可显形"的保证。"""
    import direction_drift.scenario as sc

    kwargs = {k: v for k, v in CARD.items() if k != "prompt_version"}
    before = sc.card_fingerprint(**kwargs)
    old = sc.FINGERPRINT_VERSION
    try:
        sc.FINGERPRINT_VERSION = "cTEST"
        assert sc.card_fingerprint(**kwargs) != before, (
            "换了算法版本指纹却没变——版本没进指纹串，历史指纹将无法与新指纹区分")
    finally:
        sc.FINGERPRINT_VERSION = old


def test_card_method_and_free_function_same_source(card):
    """类方法与独立函数必须同源——检测页没有 pydantic 对象，只能调独立函数。"""
    from direction_drift.scenario import card_fingerprint

    kwargs = {k: v for k, v in CARD.items() if k != "prompt_version"}
    assert card_fingerprint(**kwargs) == card.fingerprint()


@pytest.mark.parametrize("field,new_value", [
    ("card_id", "another-card-v1"),
    ("goal", "换一个完全不同的目标"),
    ("core_samples", [s + "（改）" for s in CARD["core_samples"]]),
    ("boundary_samples", CARD["boundary_samples"] + ["多一条边界样本"]),
    ("negative_samples", [s + "（改）" for s in CARD["negative_samples"]]),
    ("constraints", ["换一条不一样的约束条款"]),
    ("rule_layer", [{"name": "别的规则", "pattern": "换一个(模式|正则)"}]),
    ("min_suspect_len", 8),
    ("format_whitelist", "^另一个[:：]"),
])
def test_fingerprint_covers_every_judging_field(card, field, new_value):
    """每个参与判定的字段都必须进指纹。漏一个 = 改那个字段不被发现。"""
    d = dict(CARD)
    d[field] = new_value
    assert ScenarioCard.from_dict(d).fingerprint() != card.fingerprint(), (
        f"改动 {field} 后指纹未变——该字段没有进指纹，卡被改了也不会被发现")


def test_fingerprint_ignores_prompt_version(card):
    """提示词版本是标定侧的输入，不是卡的身份：改它不该改指纹。"""
    d = dict(CARD)
    d["prompt_version"] = "9.9"
    assert ScenarioCard.from_dict(d).fingerprint() == card.fingerprint()


def test_fingerprint_ignores_element_order(card):
    """重排样本不改变判定行为（定轴取均值、锥形取分位数，都与顺序无关），
    因此不该改指纹——否则"把样本重新排一遍"会被误报成"卡被改过"。"""
    d = dict(CARD)
    d["core_samples"] = list(reversed(CARD["core_samples"]))
    d["boundary_samples"] = list(reversed(CARD["boundary_samples"]))
    d["negative_samples"] = list(reversed(CARD["negative_samples"]))
    assert ScenarioCard.from_dict(d).fingerprint() == card.fingerprint()


def test_fingerprint_handles_mixed_constraints():
    """constraints 允许 str 与 dict 并存，规范化排序不能在这里炸。"""
    from direction_drift.scenario import card_fingerprint

    a = card_fingerprint(card_id="c", goal="g", core_samples=["x"],
                         constraints=["自由文本", {"text": "结构化", "actions": ["a"]}])
    b = card_fingerprint(card_id="c", goal="g", core_samples=["x"],
                         constraints=[{"text": "结构化", "actions": ["a"]}, "自由文本"])
    assert a == b, "同一条约束的两种书写顺序应得同一指纹"


def test_fingerprint_ignores_nested_order():
    """顺序无关性必须递归到嵌套层，只排最外层等于只做了一半。

    依据：`constraints` 里 dict 的列表（actions / objects）顺序不参与判定——
    `core/constraint_checker._rule_check` 是逐词 `output.find(act)`、命中即返回，
    **violated 的真假与顺序无关**（差别仅在理由文本里出现的是哪一个动作）。
    不做嵌套规范化的话，仅重排 actions 就会被报成"卡被改过"，
    与本节要防的"仅重排假阳性"是同一类。
    """
    from direction_drift.scenario import card_fingerprint

    a = card_fingerprint(
        card_id="c", goal="g", core_samples=["x"],
        constraints=[{"actions": ["代下单", "帮你下单"], "objects": ["酒店", "机票"]}])
    b = card_fingerprint(
        card_id="c", goal="g", core_samples=["x"],
        constraints=[{"actions": ["帮你下单", "代下单"], "objects": ["机票", "酒店"]}])
    assert a == b, "嵌套列表重排不该改指纹（判定结果与 actions 顺序无关）"


def test_fingerprint_nested_order_is_not_over_loosened():
    """递归规范化不得把"真改动"也吃掉：嵌套内容变了，指纹仍必须变。

    这是上一条的**判别力对照**——只证明"重排不变"是不够的，
    一个恒返回常量的实现也能让它通过；必须同时证明"改内容会变"。
    """
    from direction_drift.scenario import card_fingerprint

    a = card_fingerprint(card_id="c", goal="g", core_samples=["x"],
                         constraints=[{"actions": ["代下单", "帮你下单"]}])
    b = card_fingerprint(card_id="c", goal="g", core_samples=["x"],
                         constraints=[{"actions": ["代下单", "帮你退款"]}])
    assert a != b, "嵌套内容变了指纹却没变——递归规范化吃掉了真改动"


def test_norm_keeps_scalar_and_none_types_stable():
    """规范化不得顺手把类型也改掉：`None` 与字符串 `"None"`、数字与数字串
    必须仍然可区分，否则两种不同的卡会算出同一指纹。"""
    from direction_drift.scenario import card_fingerprint

    base = dict(card_id="c", goal="g", core_samples=["x"])
    assert (card_fingerprint(**base, format_whitelist=None)
            != card_fingerprint(**base, format_whitelist="None"))
    assert (card_fingerprint(**base, min_suspect_len=12)
            != card_fingerprint(**base, min_suspect_len=13))


def test_fingerprint_normalizes_equivalent_empty_whitelist():
    """等价值归一：`format_whitelist` 的 `None` 与 `""` 判定完全等价，必须得同一指纹。

    依据不是"看起来差不多"，而是可核的机制：`_whitelist()` 里写的是
    `self.format_whitelist or r"(?!x)x"` —— 两者同为 falsy，编译结果完全相同，
    判定行为一字不差。所以"把空值换一种写法"被报成「卡被改过」是假阳性，
    与重排样本族是同一类：**判定行为没变，指纹却变了。**

    与上一条配对读：上一条守「不许把不同类型归一」，本条守「该归一的等价值不许漏」。
    只测一边都证明不了归一化的边界是对的。
    """
    from direction_drift.scenario import card_fingerprint

    base = dict(card_id="c", goal="g", core_samples=["x"])
    assert (card_fingerprint(**base, format_whitelist=None)
            == card_fingerprint(**base, format_whitelist="")), (
        "None 与 空串 的判定行为相同，指纹却不同——属「等价值未归一」的假阳性")

    # 归一的前提本身要成立才谈得上归一；引擎若哪天改成区别对待 ""，这条先红，
    # 让人回来重审这个"等价"判断，而不是让前提在沉默中失效。
    c_none = ScenarioCard.from_dict(dict(CARD, format_whitelist=None))
    c_empty = ScenarioCard.from_dict(dict(CARD, format_whitelist=""))
    assert (c_none._whitelist().pattern == c_empty._whitelist().pattern), (
        "两种空值的编译结果不再相同——归一的依据已不成立，本用例的前提须重审")

    # 判别力对照：归一不得顺手吃掉"非空且不同"的白名单
    assert (card_fingerprint(**base, format_whitelist=None)
            != card_fingerprint(**base, format_whitelist="^计分[:：]")), (
        "归一过度：一个空值与一条真白名单被并成了同一指纹")


# ---------------- 未知字段必须报错（extra="forbid"，2026-10-01） ----------------
# 起因：pydantic 默认的 extra="ignore" 是"默认值兜底"最彻底的形态——
# 连"有东西被丢弃"都不说。实测代价：把 rule_layer 写成 rule_layers（多一个 s），
# 加载成功、建锥成功、指纹照算，而 rule_hit('foo') 静默返回 None——
# **这张卡少了一整层检测能力，从外面一点异常都看不出来。**
# 声明式数据最怕的就是这种"看起来对"。

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples" / "cards"


def test_examples_exist_to_check():
    """自检：示例卡目录空掉时，下面那条参数化会静默变成"零个用例"（永远不红）。"""
    assert list(EXAMPLES.glob("*.json")), f"未找到官方示例卡：{EXAMPLES}"


def test_unknown_top_level_field_is_rejected():
    with pytest.raises(ValidationError):
        ScenarioCard.from_dict(dict(CARD, rule_layers=[]))       # 拼错：多了一个 s


def test_unknown_rule_layer_field_is_rejected():
    bad = dict(CARD, rule_layer=[{"name": "x", "pattern": "y", "enabled": True}])
    with pytest.raises(ValidationError):
        ScenarioCard.from_dict(bad)


def test_unknown_constraint_key_is_rejected():
    """`constraints` 是 `Union[str, dict]`，dict 不受模型字段约束——
    这是 extra="forbid" 覆盖不到的最后一道缝。

    而它漏起来更要命：`actions` 写少一个 s，`constraint_checker._declared_actions`
    就返回空，判定**静默退回启发式猜动作词**（heuristic=True），
    从页面到指纹全都看不出异常。
    """
    bad = dict(CARD, constraints=[{"text": "不代下单", "action": ["帮你下单"]}])
    with pytest.raises(ValidationError) as ei:
        ScenarioCard.from_dict(bad)
    assert "action" in str(ei.value), "报错信息应点名那个拼错的键"


def test_known_constraint_keys_still_pass():
    """对照：合法键必须放行，否则上面那条就变成了"一律拒绝"的假守卫。"""
    ok = dict(CARD, constraints=[
        "自由文本约束",
        {"text": "不索要凭证", "actions": ["发我"], "objects": ["密码"], "note": "说明"},
    ])
    ScenarioCard.from_dict(ok)                                    # 不抛即通过


def test_notes_is_metadata_and_does_not_enter_fingerprint():
    """收编 `notes` 是 extra="forbid" 的附带动作，它**不能改变卡的身份**——
    否则"给卡加一行给人读的说明"会被报成"这张卡被改过"，与重排样本族同类。"""
    a = ScenarioCard.from_dict(CARD)
    b = ScenarioCard.from_dict(dict(CARD, notes="一句给人读的说明"))
    assert b.notes == "一句给人读的说明"
    assert a.fingerprint() == b.fingerprint(), "元信息不得进指纹"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_official_example_cards_load_under_forbid(path):
    """**从真实文件读**，不看夹具。

    extra="forbid" 一开，官方示例卡若还带未收编的字段就会红——这条把
    "示例卡"和"模型定义"绑在了一起。加字段的人会在 CI 里当场知道，
    而不是等用户加载时报错。
    """
    c = ScenarioCard.from_file(path)
    assert c.card_id
    assert c.fingerprint().startswith("c2:")

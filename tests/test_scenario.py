# -*- coding: utf-8 -*-
"""scenario.py 测试：场景卡加载、建锥、规则层、组合判据。"""
import json

import pytest

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

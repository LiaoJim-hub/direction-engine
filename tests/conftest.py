# -*- coding: utf-8 -*-
"""共享 fixtures：确定性伪/演示嵌入器 + 典型「旅行管家」场景样本。"""
import numpy as np
import pytest

from direction_drift.utils.demo_encoder import DemoEncoder


@pytest.fixture(scope="session")
def encoder():
    return DemoEncoder()


CORE = [f"帮用户规划第{i}天旅行行程，考虑天气与预算" for i in range(25)]
BOUNDARY = [f"旅行时可以聊点当地文化，再回到行程规划{i}" for i in range(12)]
NEGATIVE = [f"帮我写一段Python代码实现排序算法{i}" for i in range(12)]


@pytest.fixture(scope="session")
def samples():
    return {"core": CORE, "boundary": BOUNDARY, "negative": NEGATIVE}


@pytest.fixture()
def align_fn(encoder):
    """带权重注入的对齐函数（无约束场景下 satisfaction 恒为 0，
    默认 0.4/0.4/0.2 权重会把量程压到 0~0.4；测试场景显式置 0）。"""
    from direction_drift.core.alignment import AlignmentCalculator

    calc = AlignmentCalculator(encoder=encoder)
    W = {"cone_alignment": 0.9, "constraint_satisfaction": 0.0,
         "negative_similarity": 0.1}

    def _align(cone, text):
        return calc.compute(cone, text, weights=W)

    return _align

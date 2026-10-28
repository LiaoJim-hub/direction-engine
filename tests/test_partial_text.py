# -*- coding: utf-8 -*-
"""横切的守卫：`compute()` 的「部分文本」入参（2026-10-29）。

★ 为什么它不是一个布尔就够了（★ 这是这一轮核出来的）：
  `compute()` 算的四个量里，**两个对文本长度不敏感**（归一化后与长度无关），
  **两个敏感**（★ 因为 `constraint_checker.check(text, ...)` 吃原文 ——
  约束可能在后半段才出现）。
★★ 故「部分文本」如果不分开，`constraint_satisfaction` 会**系统性偏低**
  ⇒ `overall_alignment` 偏低 ⇒ **假阴性 ⇒ 误报漂移**。
★★★ 而那是「不许静默判绿」的对偶：**也不许静默判红**。

故本文件守四条：
  ① `is_partial=False` 时，行为与今天**逐字相同**（★ 不改旧路）
  ② `is_partial=True` 时，`constraint_satisfaction` 与 `overall_alignment` **不可核**
  ③ 而**方向那两个量仍给**（★ 它们是有意义的 —— 归一化后与长度无关）
  ④ 而"不可核"**不许**被写成 0 / None 冒充"低分"（★ 要能被读出来）
"""
import importlib
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def al():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("direction_drift.core.alignment")


class 假编码器:
    """★ 定长向量：同一段文本 → 同一向量（★ 而**不同长度**会给不同向量）。"""

    def encode(self, text):
        v = np.zeros(8, dtype=float)
        for i, ch in enumerate(text or ""):
            v[i % 8] += (ord(ch) % 17) / 17.0
        if np.linalg.norm(v) < 1e-9:
            v[0] = 1.0
        return v


class 假约束:
    """★ 约束"命中"了才给 1 —— ★ 故**部分文本**会**没命中**（那正是那个坑）。"""

    def __init__(self, 需含: str):
        self.需含 = 需含

    def check(self, text, constraints):
        ok = self.需含 in (text or "")
        return {"satisfaction": 1.0 if ok else 0.0, "violations": [] if ok else ["缺"]}


class 假锥:
    def __init__(self, axis):
        self.axis = np.asarray(axis, dtype=float)
        self.constraints = ["x"]
        self.goal = "g"

    def get_text_embeddings(self, enc):
        return {"negatives": []}

    def contains(self, out):
        return {"position": "inside", "angle": 10.0}


def _算(al, text, is_partial):
    c = al.AlignmentCalculator(encoder=假编码器(),
                               constraint_checker=假约束("末尾那句"))
    return c.compute(假锥([1, 0, 0, 0, 0, 0, 0, 0]), text,
                     is_partial=is_partial) if "is_partial" in c.compute.__code__.co_varnames \
        else c.compute(假锥([1, 0, 0, 0, 0, 0, 0, 0]), text)


# ══════════════════════════════════════════════════════════════════════════
# ① is_partial=False ⇒ 与今天逐字相同
# ══════════════════════════════════════════════════════════════════════════
def test_full_text_keeps_signature_shape(al):
    """★ 旧路不改：`compute(cone, text)` 仍然可用（★ 不给 is_partial 也不炸）。"""
    c = al.AlignmentCalculator(encoder=假编码器(), constraint_checker=假约束("末尾那句"))
    r = c.compute(假锥([1, 0, 0, 0, 0, 0, 0, 0]), "末尾那句在这")
    assert "overall_alignment" in r and "text_len" in r


# ══════════════════════════════════════════════════════════════════════════
# ② ③ is_partial=True ⇒ 两个量不可核，而方向那两个仍给
# ══════════════════════════════════════════════════════════════════════════
def test_partial_marks_constraint_uncomputable(al):
    """★★ `constraint_satisfaction` 在部分文本下**必须**被标成不可核 ——

    ★ 因为它吃原文，而**后半段的约束还没到** ⇒ 偏低 ⇒ **假阴性**。
    """
    c = al.AlignmentCalculator(encoder=假编码器(), constraint_checker=假约束("末尾那句"))
    r = c.compute(假锥([1, 0, 0, 0, 0, 0, 0, 0]), "前半句", is_partial=True)
    assert r.get("constraint_satisfaction") is None, (
        "★ 部分文本下 `constraint_satisfaction` 仍给了数 —— 而它**系统性偏低**，"
        "会让 overall 偏低 ⇒ 误报漂移。实际：{}".format(r.get("constraint_satisfaction")))
    assert r.get("constraint_uncomputable") is True, (
        "★ 它没有**标出来**。★ 而 `None` 本身可以读成「低分」，故必须有一个显式的标")


def test_partial_marks_overall_uncomputable(al):
    """★★ `overall_alignment` 含 constraint 那项 ⇒ 同一条理。"""
    c = al.AlignmentCalculator(encoder=假编码器(), constraint_checker=假约束("末尾那句"))
    r = c.compute(假锥([1, 0, 0, 0, 0, 0, 0, 0]), "前半句", is_partial=True)
    assert r.get("overall_alignment") is None, (
        "★ overall 在部分文本下仍给了数 —— 而它含 constraint 那项 ⇒ 不可比")
    assert r.get("is_partial") is True, "★ 结果里该能看出这是部分文本"


def test_partial_still_gives_directional_numbers(al):
    """★★★ 而**方向那两个量仍给** —— ★ 它们**是有意义的**（归一化后与长度无关）。"""
    c = al.AlignmentCalculator(encoder=假编码器(), constraint_checker=假约束("末尾那句"))
    r = c.compute(假锥([1, 0, 0, 0, 0, 0, 0, 0]), "前半句", is_partial=True)
    assert r.get("cone_alignment") is not None, (
        "★ `cone_alignment` 也被抹掉了 —— 而它是**可算的**（★ 归一化后与长度无关）")
    assert r.get("negative_similarity") is not None, "★ `negative_similarity` 同理"
    assert r.get("cone_position") is not None, "★ `cone_position` 同理"


# ══════════════════════════════════════════════════════════════════════════
# ④ 而"不可核"不许冒充低分
# ══════════════════════════════════════════════════════════════════════════
def test_partial_is_distinguishable_from_low_score(al):
    """★★★ 这条是这一整个文件的命门：

    `None` 与 `0.0` 在 Python 里**都能被 `is None` 之外的写法混淆**
    （`max(0.0, ...)` · `sum()` · `or 0`）。故必须能区分：
      · 全量文本而约束没命中 ⇒ overall = **0.0**（★ 那是真的低）
      · 部分文本             ⇒ overall = **None** + `is_partial=True`（★ 那是不可核）
    """
    c = al.AlignmentCalculator(encoder=假编码器(), constraint_checker=假约束("末尾那句"))
    锥 = 假锥([1, 0, 0, 0, 0, 0, 0, 0])
    全量_没命中 = c.compute(锥, "这句话里没有那个短语", is_partial=False)
    部分 = c.compute(锥, "这句话里没有那个短语", is_partial=True)
    assert 全量_没命中["overall_alignment"] is not None, "★ 全量下该给数（哪怕是低分）"
    assert 部分["overall_alignment"] is None, "★ 部分下该给 None"
    assert 部分.get("is_partial") is True and 全量_没命中.get("is_partial") is not True

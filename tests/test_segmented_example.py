# -*- coding: utf-8 -*-
"""示范件 `examples/分段判定.py` 的守卫（2026-10-29）。

★ 它守三件（★ 而第三件是这一整个方向上的铁律）：
  ① 切段只切、不判（★ 判是调用方的事）
  ② 只有**最后一段**是完整文本 —— 前面那些段 `overall_alignment` 为 `None`
  ③ ★★★ `act()` 可以被调多次，而**它一次都没有执行过"再选"**
      （`return_log()` 为空 —— 那是根协议 3.0:29 的机器读数）
"""
import importlib.util
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _载示范件():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    f = ROOT / "examples" / "分段判定.py"
    spec = importlib.util.spec_from_file_location("分段判定_被测", f)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class 假编码器:
    def encode(self, text):
        v = np.zeros(8, dtype=float)
        for i, ch in enumerate(text or ""):
            v[i % 8] += (ord(ch) % 17) / 17.0
        if np.linalg.norm(v) < 1e-9:
            v[0] = 1.0
        return v


class 假约束:
    def __init__(self, 需含="尾"):
        self.需含 = 需含

    def check(self, text, constraints):
        ok = self.需含 in (text or "")
        return {"satisfaction": 1.0 if ok else 0.0, "violations": [] if ok else ["缺"]}


class 假锥:
    def __init__(self):
        self.axis = np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=float)
        self.constraints = ["x"]
        self.goal = "g"
        self.frozen = False
        self.frozen_at = None
        self.freeze_reason = None

    def get_text_embeddings(self, enc):
        return {"negatives": []}

    def contains(self, out):
        return {"position": "inside", "angle": 5.0}

    def freeze(self, r):
        self.frozen = True
        self.frozen_at = "t"
        self.freeze_reason = r

    def unfreeze(self):
        self.frozen = False


# ══════════════════════════════════════════════════════════════════════════
def test_split_only_splits():
    """★ ① 切段只切 —— ★ 而它是**可替换的**（★ 故它不该判任何东西）。"""
    m = _载示范件()
    段 = m.按句切("第一句。第二句！第三句？", 100)
    assert len(段) == 3, "★ 按标点该切成 3 段。实得：{}".format(段)
    assert "".join(段) == "第一句。第二句！第三句？", "★ 切段不该丢字"


def test_only_last_segment_is_complete():
    """★★ ② **只有最后一段**是完整文本 —— ★ 前面那些段 `overall_alignment` 必须是 None。

    ★★ 因为只有最后一段拿得到后文（★ 约束可能在后半段才出现）。
    """
    m = _载示范件()
    al = importlib.import_module("direction_drift.core.alignment").AlignmentCalculator(
        encoder=假编码器(), constraint_checker=假约束("尾"))
    out = m.分段判(al, 假锥(), "第一段。第二段。第三段结尾。", 每段字數=4)
    assert out["段数"] >= 2, "★ 该切成多段"
    for r in out["逐段"]:
        if r["is_partial"]:
            assert r["overall_alignment"] is None, (
                "★ 部分段给了 overall —— 而它**不可比**")
        else:
            assert r["overall_alignment"] is not None, (
                "★ 最后一段该给 overall —— 而它是 `None`")
    assert out["可比段数"] == 1, "★ 可比段只该有 1 段（最后那一段）"


def test_direction_numbers_kept_on_partial():
    """★ 而方向量在**每一段**都该给 —— ★ 它们是可看的（「它现在朝哪」）。"""
    m = _载示范件()
    al = importlib.import_module("direction_drift.core.alignment").AlignmentCalculator(
        encoder=假编码器(), constraint_checker=假约束("尾"))
    out = m.分段判(al, 假锥(), "第一段。第二段。第三段。", 每段字數=4)
    assert out["方向量段数"] == out["段数"], (
        "★ 有段没拿到方向量 —— 而它在部分文本下**是可算的**")


def test_act_called_multiple_times_but_never_resumes():
    """★★★ ③ 铁律：`act()` 被调多次，而**一次都没执行"再选"**。

    ★ 判据是机器的：`return_log()` **必须为空** ——
      因为它只记 `resume()` / `revise()`，而那两个是"返回动作"。
    ★★ 故若它非空 ⇒ 有东西**自动做了再选** ⇒ 违反根协议 3.0:29。
    """
    m = _载示范件()
    from direction_drift.core.drift_detector import DriftDetector
    from direction_drift.core.return_protocol import ReturnProtocol
    al = importlib.import_module("direction_drift.core.alignment").AlignmentCalculator(
        encoder=假编码器(), constraint_checker=假约束("尾"))
    锥 = 假锥()
    det = DriftDetector(window=2)
    proto = ReturnProtocol()
    out = m.分段并判(det, proto, al, 锥, "第一段。第二段。第三段。第四段。", 每段字數=4)
    assert out["return_log"] == [], (
        "★★★ `return_log()` 非空 ⇒ 有东西**自动执行了「再选」** —— "
        "违反根协议 3.0:29。实见：{}".format(out["return_log"]))
    # ★ 而 stream_log 可以非空（★ 那是"停/看"，它允许在流式中发生）
    assert isinstance(out["stream_log"], list)


def test_probe_return_log_would_red():
    """★ 探针：`return_log() == []` 这条判据**真的会红**（★ 因为 resume() 会往里写）。"""
    from direction_drift.core.return_protocol import ReturnProtocol
    p = ReturnProtocol()
    class 检测器:
        def __init__(self):
            self.history = []; self.archive = []; self.means = []
            self._cusum_s = 0.0; self.state = "running"
        def reset(self):
            pass
    p.act({"drift_level": "drift", "history_size": 30, "needed": 20}, cone=假锥())
    p.resume(假锥(), 检测器())
    assert p.return_log() != [], "★ `resume()` 没往 return_log 里写 —— 探针本身写错了"

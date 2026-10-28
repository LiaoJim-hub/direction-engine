# -*- coding: utf-8 -*-
"""纵切第一步的守卫（2026-10-29）。

★ 目标（按刚写死的定义）：**让「停/看」第一次在账里出现**。
★★ 现状：`_log_return` 只有两个 trigger 值（`resume()` / `revise()`），**两个都是人触发的** ——
   而**`act()`（唯一在"流式中"被调的那一处）一处都没落痕**。
★★★ 故这一版的判据只有一个：**`act()` 在"停/看"那三个分支上，落一条 `trigger="stream()"` 的痕**。

★ 而它**不许**做的事（根协议 3.0:29）：「再选不能被自动执行」——
  故 `act()` 里**任何**自动改方向 / 自动 `state = "running"` 都**不许**出现。
"""
import importlib
import inspect
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rp():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("direction_drift.core.return_protocol")


class 假锥:
    """★ 只够 `act()` 用（★ 而它**不许**在 `act()` 里被自动改）。"""

    def __init__(self):
        self.goal = "g"
        self.frozen = False
        self.frozen_at = None
        self.freeze_reason = None
        self.freeze_次 = 0

    def freeze(self, reason):
        self.frozen = True
        self.frozen_at = "t"
        self.freeze_reason = reason
        self.freeze_次 += 1

    def unfreeze(self):
        self.frozen = False


# ══════════════════════════════════════════════════════════════════════════
# ① 而那三个分支必须落痕，且 trigger 是 "stream()"
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("level,期望action", [
    ("warning", "observe"),
    ("drift", "pause"),
    ("confirmed_drift", "return"),
])
def test_act_logs_stream_trigger(rp, level, 期望action):
    """★★ 那三个分支各落一条痕，而 `trigger` 必须是 `stream()`。"""
    p = rp.ReturnProtocol()
    锥 = 假锥()
    前 = len(p.stream_log())
    out = p.act({"drift_level": level, "history_size": 30, "needed": 20}, cone=锥)
    assert out["action"] == 期望action, "★ action 变了：{}".format(out)
    后 = p.stream_log()
    assert len(后) > 前, (
        "★ `act()` 在 `{}` 上**没落痕** —— 故「停/看发生在流式中」这件事在账里仍然是 0".format(level))
    新 = 后[前:]
    assert all(r.get("trigger") == "stream()" for r in 新), (
        "★ 落了痕，但 trigger 不是 `stream()` —— 实见：{}".format(
            [r.get("trigger") for r in 新]))


def test_act_does_not_log_on_normal(rp):
    """★★ `normal` 不该落痕 —— ★ 因为**没发生停/看**（★ 落了就是把噪音计成动作）。"""
    p = rp.ReturnProtocol()
    锥 = 假锥()
    # ★ 先让它处于 running（默认就是）
    前 = len(p.stream_log())
    out = p.act({"drift_level": "normal", "history_size": 30, "needed": 20}, cone=锥)
    assert out["action"] == "continue"
    assert len(p.stream_log()) == 前, "★ `normal` 也落痕了 —— 那会把「什么都没发生」计成一次停/看"


# ══════════════════════════════════════════════════════════════════════════
# ② 那条铁律：act() 不许自动执行「再选」
# ══════════════════════════════════════════════════════════════════════════
def test_act_never_auto_resumes(rp):
    """★★★ 根协议 3.0:29「再选不能被自动执行」——

    ★ 实测法：让 `act()` 走完**全部** level，而 `state` **永远不许**回到 `running`
      （★ 除非它本来就是 running）。
    """
    levels = ["insufficient_history", "no_output", "too_short", "uncalibrated",
              "normal", "warning", "drift", "confirmed_drift"]
    p = rp.ReturnProtocol()
    # ★ 先把它推到停态（★ 用 drift 那一路）
    p.act({"drift_level": "drift", "history_size": 30, "needed": 20}, cone=假锥())
    停态 = p.state
    assert 停态 != "running", "★ 前提不成立：drift 之后它竟是 running"
    for lv in levels:
         p.act({"drift_level": lv, "history_size": 30, "needed": 20}, cone=假锥())
         assert p.state != "running", (
             "★★★ `act()` 在 `{}` 上把 state 自动推回了 running —— **那是自动执行「再选」**，"
             "★ 违反根协议 3.0:29".format(lv))


def test_act_never_rebuilds_cone(rp):
    """★★ 而它也不许自动**改方向** —— ★ 用源码守（★ 因为一个 rebuild 调用比 state 更隐蔽）。"""
    源 = inspect.getsource(rp.ReturnProtocol.act)
    有效 = "\n".join(x for x in 源.split("\n") if not x.lstrip().startswith("#"))
    for 禁 in ("rebuild_from_samples", "rebuild(", "cone.rebuild"):
        assert 禁 not in 有效, (
            "★ `act()` 里出现了 `{}` —— 那是**自动改方向**，属「再选」，不许自动".format(禁))


# ══════════════════════════════════════════════════════════════════════════
# ③ 而那三个值，账里能数得出来
# ══════════════════════════════════════════════════════════════════════════
def test_two_logs_are_separate(rp):
    """★★ 两个账**分开** —— ★ 而那不是洁癖：它让
    
eturn_log() == []那条旧断言**不得不改**。

    ★★★ 而它也正是定义件第四节那句的结构后果：
      「trigger 不记「返回」，只记「停/看」在哪个时点」。
    """
    p = rp.ReturnProtocol()
    assert p.return_log() == [], "★一开始两个账都该空"
    assert p.stream_log() == []
    p.act({"drift_level": "drift", "history_size": 30, "needed": 20}, cone=假锥())
    assert len(p.stream_log()) == 1, "★停/看该落一条痕"
    assert p.return_log() == [], (
        "★★ 返回动作的账**仍该为空** —— 因为 act() 没有执行返回（再选不许自动）")


class _假检测器:
    """`resume()` 需要的最小接口。"""

    def reset(self):
        pass

    def __init__(self):
        self.history = []
        self.archive = []
        self.means = []
        self.state = "running"

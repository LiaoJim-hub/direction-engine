# -*- coding: utf-8 -*-
"""示范件：**分段判定**（乙档的「本仓那一半」怎么被调用方用起来）。

★★ 为什么这是「示范件」而不是「机制」——
  思考台 2026-10-29 逐字：「`direction-engine` 里没有「轮」。`act()` 次数是「**动作次数**」，
  demo/CLI 是「**进程次数**」，两个都不是轮。**轮只存在于有会话的那一侧。**」
  ★★ 故本文件**不定义轮** —— 它只演示：**一个调用方**拿到一长段文本时，
  怎么**自己**切段、自己决定每段算什么、自己把读数攒起来。

★★★ 而它同时演示三件不许省的事：
  ① 每段都传 `is_partial=True`（★ 除了**最后一段** —— 只有它才是完整的）
  ② 部分段的 `overall_alignment` **不许**与全量比（★ 它是 `None`）
  ③ `act()` 可以在一轮里被调多次 —— 而**每一次都只做「停/看」，不许自动「再选」**
"""
from __future__ import annotations

import numpy as np

from direction_drift.core.alignment import AlignmentCalculator
from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.return_protocol import ReturnProtocol


# ══════════════════════════════════════════════════════════════════════════
# ① 切段：**调用方**的事（★ 而这是本文件唯一"自己定义"的东西）
# ══════════════════════════════════════════════════════════════════════════
def 按句切(text: str, 每段字數: int = 40) -> list[str]:
    """把一段文本切成若干段 —— ★ **判据是"句号/问号/感叹号 或 到字数上限"**。

    ★★ 而它刻意**不是**"按 token" —— 因为**那会把这个库绑死在一个编码器上**。
    ★★★ 而它刻意**是可替换的** —— 调用方可以用它自己的切法（★ 本文件只是**一个**例子）。
    """
    段, 当前 = [], ""
    for ch in text:
        当前 += ch
        if ch in "。！？\n" or len(当前) >= 每段字數:
            段.append(当前)
            当前 = ""
    if 当前.strip():
        段.append(当前)
    return 段 or [text]


# ══════════════════════════════════════════════════════════════════════════
# ② 分段判：**每段各算一次**，而读数分两类攒
# ══════════════════════════════════════════════════════════════════════════
def 分段判(cal: AlignmentCalculator, cone: DirectionCone, 全文: str,
           每段字數: int = 40) -> dict:
    """★ 这条就是乙档的示范：**同一次生成里，`compute()` 被调多次**。

    ★★ 而关键一条：**只有最后一段**传 `is_partial=False` ——
       因为只有它**真的是完整的**；前面那些段拿不到后文。
    ★★★ 故前面那些段的 `overall_alignment` 会是 `None` ——
       **而那不是缺陷，那是如实**。
    """
    段 = 按句切(全文, 每段字數)
    读数 = []
    for i, c in enumerate(段):
        是最后一段 = (i == len(段) - 1)
        r = cal.compute(cone, c, is_partial=not 是最后一段)
        读数.append({"i": i, "text_len": r.get("text_len"),
                     "is_partial": not 是最后一段,
                     "cone_alignment": r.get("cone_alignment"),
                     "overall_alignment": r.get("overall_alignment"),
                     "not_computable": r.get("not_computable", [])})
    # ★ 而"可比的"只有全量那一段（★ 见 docs/定义件）
    可比 = [r for r in 读数 if not r["is_partial"]]
    return {"段数": len(段), "逐段": 读数,
            "可比段数": len(可比),
            "方向量段数": sum(1 for r in 读数 if r["cone_alignment"] is not None),
            "★ 边界声明": ("逐段的 cone_alignment 是「它现在朝哪」——可看；"
                          "而逐段的 overall_alignment 在部分文本下**不可核** —— 不可比。")}


# ══════════════════════════════════════════════════════════════════════════
# ③ 而 act() 在一轮里被调多次 —— ★ 而它每次只做「停/看」
# ══════════════════════════════════════════════════════════════════════════
def 分段并判(det: DriftDetector, proto: ReturnProtocol,
            cal: AlignmentCalculator, cone: DirectionCone, 全文: str,
            每段字數: int = 40) -> dict:
    """★ 乙档的**完整**示范：每段既算对齐（横切），也过 `act()`（纵切）。

    ★★ 而**铁律**（根协议 3.0:29）：`act()` 在这一整轮里
       **只许让「发现」更早，不许让「决定」自动发生** ——
       故本函数**不**在任何分支里改方向、不调 `resume()` / `revise()`。
    ★★★ 故它的返回里**没有**"已恢复"这种字 —— 只有"该你看了"。
    """
    段 = 按句切(全文, 每段字數)
    轨 = []
    for i, c in enumerate(段):
        是最后一段 = (i == len(段) - 1)
        al = cal.compute(cone, c, is_partial=not 是最后一段)
        # ★★★ 2026-10-29（A 自查 · 实测撞到）：**部分段不喂 judge()**。
        #   病灶：`DriftDetector.judge()` 在 `score = alignment["overall_alignment"]` 那一行
        #   把 `None` 当数字加 ⇒ `TypeError`（实测：unsupported operand type(s) for +: NoneType）。
        #   ★★ 而那**不是 judge() 的 bug** —— 它处理了 `invalid`（空输出/零向量），
        #      而 `is_partial` 是**第三种情形**（不是「无效」，是「不可核」），它在这一轮之前不存在。
        #   ★★★ 故本示范件的形态是：**部分段只算方向量，不判漂移；只有完整段才喂 judge()。**
        #      而那不是退让 —— 那正是 `is_partial` 说的事：部分文本的可核性只有方向那一段。
        if not 是最后一段:
            轨.append({"i": i, "is_partial": True,
                       "drift_level": "not_judged",
                       "cone_alignment": al.get("cone_alignment"),
                       "action": "continue", "state": proto.state})
            continue
        d = det.judge(al)
        a = proto.act(d, cone=cone, alignment=al)
        轨.append({"i": i, "is_partial": False,
                   "drift_level": d.get("drift_level"),
                   "action": a.get("action"),
                   "state": proto.state})
        # ★★ 而这一行是**唯一的早停**：确认漂移 ⇒ 停。
        #    ★★★ 而它**不自动做「再选」** —— 只把控制权交回调用方。
        if a.get("action") == "return":
            break
    return {"段数": len(段), "跑了几段": len(轨), "逐段": 轨,
            "stream_log": proto.stream_log(),
            "return_log": proto.return_log(),
            "★ 而 return_log 该为空": len(proto.return_log()) == 0,
            "★ 边界声明": ("`return_log()` 为空 = 本函数**没有**执行过返回动作（resume/revise）"
                          "—— 那是对的：再选不能被自动执行。"
                          "★ 而部分段**不喂 judge()** —— 因为它的 overall 不可核，"
                          "喂进去会 TypeError（实测）。")}

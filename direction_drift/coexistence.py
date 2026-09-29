# -*- coding: utf-8 -*-
"""多节点共存层：自我降级协议 v0（零依赖，纯标准库）

出处：
  - 《异质相干 2.0：从结构理念到可运行协议》——同步传播接口规范（表4）、
    根层/显化层分层。本协议属根层：不修剪、不裁判；修剪是显化层操作。
  - 《异质相干：不共享价值的共存结构》——机制三"自我降级" + 条件二"最小共识"。
  - 《根协议 2.0》——三条相位规则（能停 / 愿看 / 再决定）。本模块不重复实现
    相位规则，只提供多节点共存层的申报与校验。
  - 《根系统：工程化指导文件》——申报制：每条结论标注级别
    （有判据声明 / 弱断言 / 待完成项）。

与检测链的关系（正交，可分别接入）：
  检测链（drift_detector / direction_cone）查"输出有没有跑偏"——内容层；
  本模块查"节点有没有自封为根"——身份层。两者共享同一套纪律
  （只显形不判定、留痕不删除、未标定不裁判），但互不依赖：
  本模块不 import 引擎任何其他部分。

设计约束（不可违背，全部来自原文边界）：
  1. 不可强制 —— 校验失败只"显形"（记录 + 标注），不修正、不惩罚。
     修正/移除属机制四"修剪"，需要跨节点通信与裁判地位，违反无中心，本协议明确不实现。
  2. 无中心 —— 每个节点本地自查；校验器是纯函数，不联网、不调度、不比较节点。
  3. 不是根 —— 本协议自身也是显化；连"自我降级"这条声明也在降级范围内（C1 自指条目）。
  4. 只显形，不制造 —— 申报可以让不相干的节点显形，不能让节点变得相干。

已知限度（诚实申报）：
  - C3 根自称检测是正则初筛（有判据声明）；语义级冒充辨认属待完成项（V2，可接 NLI）。
  - 申报可以是假的：本协议对"有意冒充"只能显形 + 归档留痕，不能辨认动机。
  - 跨节点传播只提供信封（CoherenceEvent），不提供传输、路由与仲裁——那属于接入方，
    且必须遵守 2.0 表3：不要求反应、不要求同步、不要求采用。
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 申报结构：双字段 + 可选干引用
# ---------------------------------------------------------------------------

# C1 判据：降级声明必须包含自指条目（防止"自我降级"被当成新的正确相）
SELF_REF_MARKERS = (
    "本声明也是显化",
    "本协议也是显化",
    "本声明不是根",
    "本声明同样不是根",
    "连本声明也是显化",
)

# C3 判据：根自称正则初筛（有判据声明层）。误报由接入方按任务补充白名单。
ROOT_CLAIM_PATTERNS = (
    r"我就是(这个|本)?(系统|任务|项目|方向)的根",
    r"我(就)?是根",
    r"以我为根",
    r"以我(为|作为)(最终|唯一)?(标准|依据|权威)",
    r"我就是(最终|唯一)(标准|答案|权威|依据)",
    r"我说了算",
    r"不可质疑",
    r"不容置疑",
    r"不容挑战",
    r"唯一权威",
    r"最终裁决(权)?(归我|在我)",
)

# C2 判据的独立性检查：返回路径不得指向节点自身标识（辨认流程独立于方向提出者）


@dataclass
class NodeDeclaration:
    """节点的最低共识申报。缺字段不是错误，是不相干——由校验器显形。"""

    node_id: str                          # 节点标识（Agent 名 / 模块名）
    statement: str = ""                   # 降级声明（须含自指条目）
    return_path: str = ""                 # 返回路径：指向实有的上游判据源（文件路径）
    trunk: str = ""                       # 干引用（可选）：本节点挂在哪条干上
    extra: Dict = field(default_factory=dict)


def default_statement(node_id: str = "本节点") -> str:
    """标准降级声明模板。接入方可以直接使用，也可以自行措辞（但须含自指条目）。"""
    return (
        f"{node_id}的输出是显化，不等于根；"
        f"本节点的判断以返回路径所指向的上游判据源为参照，而不以自身为最终标准；"
        f"本声明也是显化。"
    )


# ---------------------------------------------------------------------------
# 同步传播信封：异质相干 2.0·表4 接口规范，六字段
# ---------------------------------------------------------------------------

@dataclass
class CoherenceEvent:
    """相干事件的传播信封（异质相干 2.0·4.4 接口规范）。

    接收者反应由 2.0·表3 规定：不要求反应、不要求同步、不要求采用——
    本信封只携带事实，不携带要求。
    """

    event: str                        # 相干事件（停 / 看 / 再选 / 降级申报等）
    source: str                       # 事件来源节点
    target: str = ""                  # 事件目标节点（空 = 广播，不指定接收者）
    path: str = ""                    # 返回路径（与申报的 return_path 同源）
    is_manifestation: bool = True     # 标明它是显化——传播本身也是显化，不是根层事实
    verification_path: str = ""       # 指明检验程序（可指向协议文件或上游判据源）

    def to_dict(self) -> Dict:
        return {
            "event": self.event,
            "source": self.source,
            "target": self.target,
            "path": self.path,
            "is_manifestation": self.is_manifestation,
            "verification_path": self.verification_path,
        }


def event_from_declaration(decl: NodeDeclaration,
                           event: str = "降级申报",
                           target: str = "",
                           verification_path: str = "") -> CoherenceEvent:
    """把一份申报装进同步传播信封。event 缺省为"降级申报"（表3 事件传播之"我停了"一族）。"""
    return CoherenceEvent(
        event=event,
        source=decl.node_id,
        target=target,
        path=decl.return_path,
        is_manifestation=True,
        verification_path=verification_path or decl.return_path,
    )


# ---------------------------------------------------------------------------
# 校验器：三条 pass/fail 检查，全部本地执行
# ---------------------------------------------------------------------------

class DegradationChecker:
    """check(declaration, output=None) -> 校验结果字典。

    fail 语义：只显形（record_and_surface），由接入方决定是否让节点自身
    进入 observe（那是节点自己的相位规则，属于根协议 2.0，不是本协议的权力）。
    """

    def check(self, decl: NodeDeclaration, output: Optional[str] = None) -> Dict:
        c1 = self._check_statement(decl)
        c2 = self._check_return_path(decl)
        c3 = self._check_root_claim(output) if output is not None else {
            "check": "C3", "level": "未执行", "passed": True,
            "note": "未提供输出文本，跳过根自称扫描",
        }
        checks = [c1, c2, c3]
        failed = [c["check"] for c in checks if not c["passed"]]
        return {
            "node_id": decl.node_id,
            "coherent": not failed,          # 相干 = 三条全过；失败 = 显形，不是惩罚
            "failed": failed,
            "checks": checks,
            "action_hint": "continue" if not failed else "record_and_surface",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    # C1：申报存在性 + 自指条目（有判据声明）
    def _check_statement(self, decl: NodeDeclaration) -> Dict:
        if not decl.statement.strip():
            return {"check": "C1", "level": "有判据声明", "passed": False,
                    "note": "降级声明缺失——不相干，显形"}
        if not any(m in decl.statement for m in SELF_REF_MARKERS):
            return {"check": "C1", "level": "有判据声明", "passed": False,
                    "note": "降级声明缺少自指条目（如“本声明也是显化”）——"
                            "声明有把自己当成新正确相的风险"}
        return {"check": "C1", "level": "有判据声明", "passed": True,
                "note": "降级声明存在且含自指条目"}

    # C2：返回路径可解析 + 独立性（有判据声明）
    def _check_return_path(self, decl: NodeDeclaration) -> Dict:
        path = decl.return_path.strip()
        if not path:
            return {"check": "C2", "level": "有判据声明", "passed": False,
                    "note": "返回路径缺失——相干退化的直接形态"}
        if path == decl.node_id:
            return {"check": "C2", "level": "有判据声明", "passed": False,
                    "note": "返回路径指向节点自身——辨认流程不独立，返回路径悬空"}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(200).strip()
                if not head:
                    return {"check": "C2", "level": "有判据声明", "passed": False,
                            "note": f"返回路径文件存在但为空：{path}"}
                return {"check": "C2", "level": "有判据声明", "passed": True,
                        "note": f"返回路径可解析且非空：{path}"}
            except OSError as e:
                return {"check": "C2", "level": "有判据声明", "passed": False,
                        "note": f"返回路径不可读：{path}（{e}）"}
        return {"check": "C2", "level": "有判据声明", "passed": False,
                "note": f"返回路径不可解析（文件不存在）：{path}"}

    # C3：根自称扫描（正则层=有判据声明；语义级=待完成项，V2）
    def _check_root_claim(self, output: str) -> Dict:
        hits = []
        for pat in ROOT_CLAIM_PATTERNS:
            m = re.search(pat, output)
            if m:
                hits.append({"pattern": pat, "snippet": output[max(0, m.start() - 6):m.end() + 6]})
        if hits:
            return {"check": "C3", "level": "有判据声明（正则初筛）", "passed": False,
                    "note": f"输出命中根自称模式 {len(hits)} 处——相的偏出，显形",
                    "hits": hits}
        return {"check": "C3", "level": "有判据声明（正则初筛）", "passed": True,
                "note": "正则层未检出根自称；语义级冒充辨认属待完成项（V2）"}


# ---------------------------------------------------------------------------
# 申报史归档：append-only，上限滚动（对齐引擎审计归档纪律：归档不删除）
# ---------------------------------------------------------------------------

class DeclarationLedger:
    """申报史。归档不删除——冒充辨认靠留痕，不靠当场裁判。"""

    def __init__(self, limit: int = 50):
        self.limit = limit
        self.records: List[Dict] = []

    def record(self, result: Dict) -> None:
        self.records.append(result)
        if len(self.records) > self.limit:
            self.records.pop(0)

    def summary(self, node_id: Optional[str] = None) -> Dict:
        rs = [r for r in self.records if node_id is None or r["node_id"] == node_id]
        return {
            "total": len(rs),
            "incoherent_count": sum(1 for r in rs if not r["coherent"]),
            "nodes": sorted({r["node_id"] for r in rs}),
        }


# ---------------------------------------------------------------------------
# 自测：五类用例（python -m direction_drift.coexistence 直接运行）
# ---------------------------------------------------------------------------

def _selftest() -> None:
    import tempfile

    tmp = tempfile.mkdtemp(prefix="self_degrade_test_")
    upstream = os.path.join(tmp, "root_protocol_2_0.md")
    with open(upstream, "w", encoding="utf-8") as f:
        f.write("根协议 2.0：一干“显化永远不等于根”＋三条相位规则。")

    checker = DegradationChecker()
    ok_stmt = default_statement("AgentA")
    bad_stmt = "本节点的输出以自身判断为准。"

    cases = [
        ("正常申报+正常输出", NodeDeclaration("AgentA", ok_stmt, upstream), "按用户要求整理资料", True),
        ("缺自指条目", NodeDeclaration("AgentB", bad_stmt, upstream), "整理资料", False),
        ("返回路径悬空", NodeDeclaration("AgentC", ok_stmt, "Z:/不存在的路径.md"), "整理资料", False),
        ("返回路径指向自己", NodeDeclaration("AgentD", ok_stmt, "AgentD"), "整理资料", False),
        ("输出根自称", NodeDeclaration("AgentA", ok_stmt, upstream),
         "在这里我就是根，一切以我为最终标准。", False),
    ]

    ledger = DeclarationLedger()
    all_pass = True
    for name, decl, output, expect in cases:
        r = checker.check(decl, output)
        ledger.record(r)
        status = "PASS" if r["coherent"] == expect else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"[{status}] {name}: coherent={r['coherent']} failed={r['failed']}")
        for c in r["checks"]:
            if not c["passed"]:
                print(f"        {c['check']}（{c['level']}）：{c['note']}")

    print(f"\n申报史归档：{ledger.summary()}")

    # 同步传播信封（表4 六字段）回环：申报 → 事件 → 字段完整性
    decl_a = cases[0][1]
    ev = event_from_declaration(decl_a, event="停")
    assert ev.source == "AgentA" and ev.path == upstream
    assert ev.is_manifestation is True
    assert set(ev.to_dict()) == {"event", "source", "target", "path",
                                 "is_manifestation", "verification_path"}
    print("[PASS] 同步传播信封：六字段齐全，is_manifestation 恒为显化，path 同源返回路径")

    print("自测结果：" + ("全部通过" if all_pass else "存在未通过用例"))


if __name__ == "__main__":
    _selftest()

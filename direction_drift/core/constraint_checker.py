"""约束检查。MVP 只启用规则层；NLI 语义判定为 V2（需内存 ≥4GB，ENABLE_NLI=1）。

约束元素支持三种形态：

1. 自由文本（**启发式，不保证**）
   `"不直接推荐具体酒店"`
   引擎剥掉否定前缀后用 jieba 取**全部**动词，任一动词在输出中出现即违规。
   已知失效模式（实测）：分词器会把动作词标成名词——"下单"被标 `n`，
   于是"不直接代用户下单、支付或预订"里唯一被检查的动词是"支付/预订"，
   输出"我直接帮你下单了"完全逃逸。**多动作约束请用形态 3。**

2. 正向式（显式假设，需 NLI）
   `{"text": "每次只问一个问题",
     "hypothesis_violation": "助手一次问了多个问题"}`

3. 结构化动作声明（**推荐**，v2.3.3 新增）
   `{"text": "不直接代用户下单、支付或预订（只给建议与清单）",
     "actions": ["代下单", "帮你下单", "直接下单", "代订", "帮你支付"],
     "note": "只给建议与清单，不得代办"}`
   引擎只做**精确子串匹配**：任一声明的动作短语出现即违规（保守、宁可误报）。
   动作词表由场景侧提供——"识别哪些词算越权动作"是场景知识，属卡，不属引擎。
   动作词要用**精确短语**（"帮你下单"）而非泛指动词（"推荐"），否则正常句也会命中。

   可选 `objects`：**一旦声明就成为配对条件**——必须"动作命中 AND 对象也在"
   才算违规。适合"X 动作 + Y 对象"才是真违规的约束（如"发我"+"身份证"），
   不声明则是纯动作匹配（更保守）。

判定状态（`violations[].status`）：
- `violated`     规则层确认违规（扣分）
- `unverifiable` 引擎**没有可用手段**判定（无声明动作词、提不出动词、无 NLI）
                 ——显形、不扣分
`ok` 的条目不会出现在列表里，但要读准它的含义：**ok 只表示"按当前手段未发现
违规"，不等于"保证合规"**。词法匹配对同义改写（"我替你把它办了"）无能为力，
NLI（V2）才治本。这个限度写在这里，是为了不让 ok 被读成"已通过检查"。

v2.3.3 修复的静默缺陷：此前 `_check_one` 在"生成了假设但 NLI 缺席"时返回
status `ok`，即**放弃判定被记成判定通过**。MVP（无 NLI）下所有非否定式约束
都走这条路，等于约束层静默失效。现在"没有手段"一律显形为 `unverifiable`。

v2.3.4 补掉它的**残留**：上面那处修的是"提不出动词"的自由文本约束；但调用方
**显式声明** `hypothesis_violation` 的约束被漏掉了——只要启发式恰好从约束文本里
提得出动词、又恰好没命中，就会走 `if evaluated` 分支报 `ok`，于是"我声明了这需要
语义判定"这件事被静默忽略（声明的检查手段不在场，却被读成"已经查过了"）。
现在显式声明 + 无 NLI 一律 `unverifiable`。
"""

import jieba.posseg as pseg
from typing import List, Tuple

NEG_PREFIX = ("不得", "不要", "不能", "不会", "不", "禁止", "别", "无需")
OUTPUT_NEG_WORDS = ("不", "别", "无需", "不用", "不会", "不再", "没")

# 否定窗口（v2.3.3：4 → 6 字符，且**只在同一小句内生效**）。
# 原实现取动词前 4 字符，两个方向都错：
# - 太短："我不会帮你下单"的"不"在 5 字符外 → 真实否定被判成违规（误报）；
# - 太宽（若单纯加长）："没关系，我直接帮你下单了"的"没"会被算进窗口 →
#   真实违规被豁免（漏报）。
# 语言上否定只在它所在的小句内管到动词，故窗口在小句分隔符处截断。
NEG_WINDOW = 6
CLAUSE_SEPARATORS = "，。！？；、,.!?;：:（）()「」“”\"' \n\t\r"


class ConstraintChecker:
    def __init__(self, nli_model=None):        # V2 接口，MVP 传 None
        self.nli = nli_model

    def check(self, output: str, constraints: List) -> dict:
        """逐条检查约束。返回 {"satisfaction", "violations"}。

        `violations` 同时承载"确认违规"与"未能判定"两类记录（`status` 区分），
        因为"没能判定"本身是需要被人看见的信息——这正是本项目的核心纪律。
        """
        if not constraints:
            return {"satisfaction": 0.0, "violations": []}
        violations, satisfaction = [], 0.0
        for c in constraints:
            text = self.text_of(c)
            hypo = c.get("hypothesis_violation") if isinstance(c, dict) else None
            violated, reason, layer, status = self._check_one(output, c, text, hypo)
            if violated:
                satisfaction -= 0.3
                violations.append({"constraint": text, "reason": reason,
                                   "layer": layer, "status": "violated"})
            elif status == "unverifiable":
                violations.append({"constraint": text, "reason": reason,
                                   "layer": "none", "status": "unverifiable"})
        return {"satisfaction": max(-1.0, satisfaction), "violations": violations}

    @staticmethod
    def text_of(constraint) -> str:
        """取约束的人读文本（结构化形态取 text，自由文本原样）。"""
        if isinstance(constraint, dict):
            return str(constraint.get("text") or constraint.get("constraint") or "")
        return str(constraint or "")

    def _check_one(self, output, constraint, text, hypothesis):
        violated, reason, evaluated = self._rule_check(output, constraint)
        if violated:
            return (True, reason, "rule", "ok")
        # 规则层未命中 → 语义层补充（V2）
        declared = hypothesis is not None          # 调用方显式声明了"违规长这样"
        if hypothesis is None:
            hypothesis = self._constraint_to_hypothesis(text)
        if hypothesis and self.nli is not None:
            nli = self._nli_check(output, hypothesis)
            if nli is not None:
                return (*nli, "nli", "ok")
            return (False, "NLI 未给出判定（entailment/contradiction 之外）",
                    "nli", "unverifiable")
        # v2.3.4：显式声明了假设、而 NLI 不在场 → **声明的检查手段缺失**，必须显形。
        # 此前这里会落到下面的 `if evaluated`，于是"启发式恰好从约束文本里提得出
        # 动词、又恰好没命中"被报告成 `ok`（已检查、未发现违规）——而声明
        # `hypothesis_violation` 本身就是"词法匹配不够用"的声明
        # （见 v2.1 审查 P1-1：正向约束靠它才可判）。这与 v2.3.3 修掉的
        # "放弃判定 ≠ 判定通过"是同一族缺陷。
        if declared:
            return (False, "调用方声明了 hypothesis_violation（需 NLI 判语义），"
                           "但 NLI 未启用（V2）：仅记录不扣分", "none", "unverifiable")
        if evaluated:
            return (False, reason, "rule", "ok")
        return (False, "规则层无可用检查手段（无声明动作词且提不出动词），"
                       "且未启用 NLI（V2）：仅记录不扣分", "none", "unverifiable")

    def _rule_check(self, output, constraint) -> Tuple[bool, str, bool]:
        """规则层。返回 `(violated, reason, evaluated)`。

        `evaluated=False` 表示**规则层没有可用的检查手段**（既无声明动作词，
        也提不出动词）——上层据此记为 `unverifiable`，而不是当作通过。
        """
        actions, objects = self._declared_actions(constraint)
        heuristic = not actions
        if heuristic:
            actions = self._guess_verbs(self.text_of(constraint))
        if not actions:
            return (False, "", False)
        for act in actions:
            idx = output.find(act)
            if idx == -1:
                continue
            # 对象词表一旦声明，就是**配对条件的一部分**：
            # 声明了 objects 的约束，必须"动作命中 AND 对象也在"才算违规。
            # 这给了建卡方一个精确度旋钮——例如"发我"这种泛用动作若单独匹配，
            # "把行程发我"会被误判；配上 objects=["身份证","验证码"] 后只在
            # 谈隐私信息时才触发。不声明 objects 则是纯动作匹配（更保守）。
            hit_obj = next((o for o in objects if o in output), None)
            if objects and hit_obj is None:
                continue
            if self._negated_in_clause(output, idx):
                return (False, "规则的动作用否定表述出现（同小句内），规则层不判", True)
            detail = f"「{hit_obj}」" if hit_obj else ""
            title = "禁止的动作短语" if not heuristic else "禁止的动作（启发式）"
            return (True, f"输出出现{title}{detail}：{act}", True)
        return (False, "", True)

    @staticmethod
    def _declared_actions(constraint) -> Tuple[List[str], List[str]]:
        """结构化声明里的动作词表与对象词表（非结构化返回空）。"""
        if not isinstance(constraint, dict):
            return [], []
        actions = [str(a) for a in (constraint.get("actions") or []) if a]
        objects = [str(o) for o in (constraint.get("objects") or []) if o]
        return actions, objects

    @staticmethod
    def _guess_verbs(constraint: str) -> List[str]:
        """自由文本约束的启发式动作词：剥否定前缀后取**全部**动词（长词优先）。

        取全部而非"最长的一个"，是 v2.3.3 的第一处修复：多动作约束
        （"推荐…并代下单"）原先只有最长那个动词被检查。
        残留局限：被分词器标成名词的动作词仍会漏（"下单" → `n`），
        故自由文本形态只是兜底，正经用法是结构化声明。
        """
        core = constraint
        for p in NEG_PREFIX:
            if core.startswith(p):
                core = core[len(p):]
                break
        verbs = [w for w, f in pseg.cut(core) if f.startswith("v") and len(w) >= 2]
        seen, out = set(), []
        for w in sorted(verbs, key=len, reverse=True):
            if w not in seen:
                seen.add(w)
                out.append(w)
        return out

    @staticmethod
    def _negated_in_clause(output: str, idx: int) -> bool:
        """动词前的小句内是否有否定词（否定只在同一小句内管到该动词）。"""
        window = output[max(0, idx - NEG_WINDOW):idx]
        cut = max((window.rfind(s) for s in CLAUSE_SEPARATORS), default=-1)
        if cut >= 0:
            window = window[cut + 1:]
        return any(w in window for w in OUTPUT_NEG_WORDS)

    def _constraint_to_hypothesis(self, constraint):
        for p in NEG_PREFIX:
            if constraint.startswith(p):
                return f"助手{constraint[len(p):]}"
        return None

    def _nli_check(self, output, hypothesis):            # V2：MVP 不走此路径
        if self.nli is None:
            return None
        try:
            label = self.nli(output, hypothesis)
            if label == "entailment":
                return (True, f"输出蕴含被禁止的行为：{hypothesis}")
            if label == "contradiction":
                return (False, "")
        except Exception:
            pass
        return None

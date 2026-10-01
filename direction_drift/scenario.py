# -*- coding: utf-8 -*-
"""场景卡：引擎与场景的接口层（v2.2.3 固化）。

架构分工（"建卡四问"，见 README 第六节）：引擎只管"怎么查"，规则内容全由
场景卡声明。一张卡 = 目标/核心/边界/反面样本 + 约束条款 + 规则层声明。
**阈值不在卡里**：high/low 是该场景标定的产物（`calibration/roc.py`），
卡只回答"查什么"，不回答"多低算偏"——所以卡能抄，阈值抄不走。
**卡是数据，不是代码**——接入一个新场景，写一张 JSON 卡即可，不改引擎。

卡结构（JSON）：
{
  "card_id": "travel-concierge-v1",
  "goal": "帮用户规划旅行行程",
  "core_samples":     ["...标准输出句..."],   # 越多越稳；20 是硬下限，示例卡刻意贴
                                              # 下限演示最小可运行配置，生产建议 30+
  "boundary_samples": ["...合法但不典型..."],
  "negative_samples": ["...典型出戏句..."],
  "constraints": [
    "不直接推荐具体酒店",                      # 自由文本：启发式，仅兜底
    {"text": "不直接代用户下单（只给建议与清单）",   # 结构化：推荐用法
     "actions": ["代下单", "帮你下单", "直接下单", "代订", "帮你支付"],
     "objects": ["酒店", "机票", "门票"],
     "note": "只给建议与清单，不得代办"}
  ],
  "rule_layer":  [{"name": "越权下单", "pattern": "直接帮你(订|下单|购买)"}],
  "min_suspect_len": 12,
  "format_whitelist": "^计分[:：]|^维度[:：]",   # 正则，合法格式行豁免
  "prompt_version": "1.0"
}

`constraints` 两种形态的分工（v2.3.3）：**要判具体动作，必须用结构化形态**。
自由文本形态靠 jieba 提动词，而分词器会把动作词标成名词（"下单" → `n`），
多动作约束还会只取到其中一个——真实违规句会整批漏判。动作词表是场景知识，
应由建卡方给出，引擎只做精确子串匹配。详见 constraint_checker 的模块 docstring。

`min_suspect_len` 与引擎的 `DriftDetector.min_judge_len` 是两件事：前者是
**场景策略**（短文本不算疑似），后者是**认识论下限**（短到无法测量方向）。
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .core.direction_cone import DirectionCone


# 结构化约束的已知键。`constraint_checker._declared_actions` 只读 actions / objects，
# text 与 note 是给人读的说明。**键名拼错必须报错**：把 actions 写成 action，
# 判定会静默退回"启发式猜动作词"，而加载、建锥、指纹三项全绿——看不出少了任何东西。
# `constraints` 是 Union[str, dict]，是 `extra="forbid"` 覆盖不到的**最后一道缝**：
# 卡顶层写错字段名会被 pydantic 拦下，但约束里写错键名不会——它会静默退回
# 启发式去猜动作词（`constraint_checker._constraint_to_hypothesis`）。
#
# ⚠️ 这份清单必须**等于执行层与展示层真实读取的键**，多一个少一个都是错的。
# 2026-10-01 首版是手抄的，漏了 `hypothesis_violation`（引擎正式支持、v2.3.4 起
# 有专门回归测试）与 `constraint`（`text` 的别名）。后果：**托管仓 30 条测试报
# ValidationError**（它的 demo 卡正好用了 `hypothesis_violation`），而本仓测试
# 全绿——示例卡恰好只用那 4 个键。这与挑刺报告 2-4 同类：
# **同一个字段被两种实现解释，两边不一致且不报错。**
# 故清单改由测试守护，键从消费端源码的读取点提取，不再手抄：
#   tests/test_constraint_layer.py::test_known_constraint_keys_cover_every_read_site
KNOWN_CONSTRAINT_KEYS = frozenset({
    "text",                  # 人读文本（主形）      —— constraint_checker.text_of 读
    "constraint",            # 同上，别名            —— constraint_checker.text_of 读
    "hypothesis_violation",  # 显式声明"违规长这样"   —— constraint_checker.check 读
    "actions",               # 结构化：禁止的动作
    "objects",               # 结构化：涉及的对象
    "note",                  # 元信息，不参与判定     —— card_page 展示时读
})


class RulePattern(BaseModel):
    # extra="forbid"：规则层是"卡的声明"，多写一个不在模式里的字段，
    # 只可能是拼错了 pattern 的名字。静默丢弃等于这张卡少一层检测能力。
    model_config = ConfigDict(extra="forbid")

    name: str
    pattern: str


class ScenarioCard(BaseModel):
    # extra="forbid"（2026-10-01）：此前是 pydantic 默认的 ignore——
    # 那是"默认值兜底"最彻底的形态：连"有东西被丢弃"都不说。
    # 实测代价：`rule_layers`（多一个 s）会被静默丢掉，`rule_hit('foo')` 返回 None，
    # 而卡的加载与建锥一切正常。声明式数据最怕的就是这种"看起来对"。
    #
    # 代价是元信息字段必须**显式收编**（见 notes）——这正是本条的用意：
    # 让"这张卡有哪些字段"这件事，有一个可数的地方。
    model_config = ConfigDict(extra="forbid")

    card_id: str
    goal: str
    core_samples: List[str] = Field(min_length=1)
    boundary_samples: List[str] = Field(default_factory=list)
    negative_samples: List[str] = Field(default_factory=list)
    constraints: List[Union[str, dict]] = Field(default_factory=list)
    rule_layer: List[RulePattern] = Field(default_factory=list)
    min_suspect_len: int = 12
    format_whitelist: Optional[str] = None
    prompt_version: str = "1.0"
    # 卡片撰写者给人读的说明（2026-10-01 显式收编）。
    # **不参与判定，也不进指纹**——它是关于这张卡的元信息，不是卡的身份。
    # 收编它是为了 extra="forbid" 能开：此前这类字段散在 JSON 里被静默丢弃。
    notes: str = ""

    @field_validator("constraints")
    @classmethod
    def _reject_unknown_constraint_keys(cls, v):
        """结构化约束里出现未知键就报错，不静默丢弃。

        与 extra="forbid" 同一条纪律，只是落点更深一层：constraints 是
        `Union[str, dict]`，dict 不受模型字段约束，是 extra="forbid" 覆盖不到的
        最后一道缝。而 `actions` 拼错一个字母，就会让这条约束**静默退化成
        启发式猜动作词**（`constraint_checker._declared_actions` 返回空 → heuristic=True），
        从外面完全看不出异常。
        """
        for i, c in enumerate(v):
            if isinstance(c, dict):
                unknown = sorted(set(c) - KNOWN_CONSTRAINT_KEYS)
                if unknown:
                    raise ValueError(
                        f"constraints[{i}] 含未知键 {unknown}；"
                        f"已知键只有 {sorted(KNOWN_CONSTRAINT_KEYS)}。"
                        f"（拼错 actions = 该约束静默失效，故在此拦下）")
        return v

    # ---------- 加载 ----------
    @classmethod
    def from_dict(cls, data: Dict) -> "ScenarioCard":
        return cls(**data)

    @classmethod
    def from_file(cls, path) -> "ScenarioCard":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ---------- 编译 ----------
    def build_cone(self, embed_fn: Callable) -> DirectionCone:
        """用卡内样本建方向锥。embed_fn: text -> 向量（生产传 Encoder().encode）。"""
        return DirectionCone.from_samples(
            goal=self.goal,
            core_samples=self.core_samples,
            boundary_samples=self.boundary_samples,
            negative_samples=self.negative_samples,
            constraints=self.constraints,
            embed_fn=embed_fn,
        )

    def _whitelist(self) -> re.Pattern:
        return re.compile(self.format_whitelist or r"(?!x)x")   # 永不匹配的空正则

    def rule_hit(self, text: str) -> Optional[str]:
        """规则层命中：返回命中描述（"规则名：详情"），未命中返回 None。

        规则层是卡的声明，不是引擎的判断——这是"异质相干：卡是数据"的落点。
        """
        for rule in self.rule_layer:
            m = re.search(rule.pattern, text)
            if m:
                detail = m.group(0)
                return f"{rule.name}：{detail}" if detail else rule.name
        return None

    def is_suspect(self, score: float, text: str, low: float) -> bool:
        """组合判据（两场景 700+ 条实测定型）：语义低分 AND 长度保护 AND
        格式白名单豁免，或规则层命中。"""
        rule = self.rule_hit(text)
        semantic = (score < low
                    and len(text) >= self.min_suspect_len
                    and not self._whitelist().search(text))
        return bool(semantic) or bool(rule)

    # ---------- 身份 ----------
    def fingerprint(self) -> str:
        """本卡指纹。写进标定产物后，卡被改动即可被发现——见 `card_fingerprint`。"""
        return card_fingerprint(
            card_id=self.card_id,
            goal=self.goal,
            core_samples=self.core_samples,
            boundary_samples=self.boundary_samples,
            negative_samples=self.negative_samples,
            constraints=self.constraints,
            rule_layer=self.rule_layer,
            min_suspect_len=self.min_suspect_len,
            format_whitelist=self.format_whitelist,
        )


# 指纹算法版本。**规范化规则一旦变更就必须递增此值。**
# 理由：指纹字符串会被写进标定产物，而「算法变了」与「卡变了」在只比较字符串时
# 完全同形——两者都表现为 `recorded != live`。不显形版本，改一次规范化规则
# 就会让全部历史指纹集体失效，并被读成"这些卡都被改过"。
# 这是本项目反复出现的同一形态（失败/变更伪装成另一种结论），故在源头堵死。
# 消费端据此把「不可比」与「不一致」分成两种状态，见 `AI方向漂移检测/calib_meta.py`。
#
# c1 → c2（2026-10-01）：规范化规则新增「等价值归一」——`format_whitelist` 的
#   `None` 与 `""` 在判定上完全等价（`_whitelist()` 用 `or` 兜底，两者同为 falsy，
#   都编译成"永不匹配"的空正则），故在指纹里统一折为 `None`。若不折，
#   "把空值换个写法"会被报成"卡被改过"，与样本族排序要防的是同一类假阳性。
#   递增的理由只有一条：这是规范化规则本身的变更，按上面那条规矩必须显形。
#   对本项目现有两张卡，digest 逐位不变（两者的 format_whitelist 都是真正则）
#   ——前缀变、值不变，恰好说明"算法版本"与"卡内容"是两件独立的事。
FINGERPRINT_VERSION = "c2"


def _canon(value) -> str:
    """把一个已规范化的值序列化成稳定 JSON 串：键排序、紧凑分隔符。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _norm(value):
    """递归规范化：dict 按键排序、list/tuple 按内容排序、标量原样。

    **为什么要递归到嵌套层**：只排最外层等于只做了一半。
    实例：`{"actions": ["a", "b"]}` 与 `{"actions": ["b", "a"]}` 会得到两个不同的
    指纹，而 `core/constraint_checker._rule_check` 是逐词 `output.find(act)`、
    命中即返回——**判定结果与 actions 顺序无关**（差别仅在理由文本里出现的是
    哪一个动作）。仅重排就报"卡被改过"，与本节要防的"仅重排假阳性"是同一类。

    **本函数的前提**：所有容器位置的顺序都不影响判定。该前提从"样本族顺序无关"
    推广到全部容器，依据是 `ScenarioCard` 现有字段都满足——样本族走
    `mean(core_embs)` 与分位数，`constraints` / `rule_layer` 逐项独立求值。
    **若将来引入位置敏感的字段**（例如按序号配对的样本权重），必须在本函数里
    为那个字段单独处理（保序），不能默默沿用这条前提——那时前提就不再成立了。
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return sorted((_norm(v) for v in value), key=_canon)
    return value


def _norm_seq(seq) -> List:
    """规范化一个「元素顺序不影响判定结果」的序列。

    保留此名供调用方复用；实际规则见 `_norm`（自 c1 起递归处理嵌套容器，
    不再只排最外层）。
    """
    return _norm(list(seq))


def card_fingerprint(*, card_id: str, goal: str,
                     core_samples=(), boundary_samples=(), negative_samples=(),
                     constraints=(), rule_layer=(),
                     min_suspect_len: int = 12,
                     format_whitelist: Optional[str] = None) -> str:
    """场景卡的规范化指纹（形如 `c1:0123456789abcdef`：算法版本 + SHA-256 前 16 位）。

    存在理由：标定产物绑定了编码器、权重、提示词版本，**唯独没有绑定卡本身**。
    而阈值是卡与数据共同作用的产物——卡一改，分数分布整体移动、原阈值随之失效，
    但标定文件不会报警，仍会自称正式标定。有了指纹，"阈值与卡是否同源"
    才从一句假设变成一件可检测的事实。

    **它答不了什么（务必一并读）**：指纹只覆盖**卡的数据**，不覆盖**环境**。
    "指纹一致"只说明卡没变，**不等于分数可复现**——分数同样会因为
    sentence-transformers / transformers / torch 换版本、或权重文件重新下载而漂移。
    要回答"分数为什么算不回来"，需要一个与卡指纹**正交**的环境指纹
    （编码器名 + 权重哈希 + 相关库版本），二者合起来才闭合。

    覆盖范围 = **卡上参与判定的数据**：样本族（定轴/减项/锥形）、约束条款、
    规则层、长度保护、格式白名单。

    **规范化的两条规则（它们与"覆盖范围"同等重要，别只读上面那句）：**

    ① **顺序无关的容器先排序**（`_norm`，递归到嵌套层）。依据是定轴走
       `mean(core_embs)`、锥形走分位数、约束逐项独立求值——都与元素顺序无关。
       不排的话，"仅重排"会被报成"卡被改过"，是假阳性。
    ② **等价值归一**：`format_whitelist` 的 `None` 与 `""` 折为同一个值。
       依据是 `_whitelist()` 里 `self.format_whitelist or r"(?!x)x"` —— 两者同为
       falsy、编译结果完全相同、判定行为一字不差。不折的话，"空值换一种写法"
       同样会被报成"卡被改过"。**与判据①是同一条纪律，只是对象从"顺序"换成了"写法"。**

    两条规则的共同判据只有一条：**判定行为相同 ⇒ 指纹必须相同**。
    反过来说，任何"改了指纹但判定行为没变"的输入，都是本函数的缺陷。

    **两处刻意的例外，写在这里以免下一个人读判据时困惑：**

    1. **`prompt_version` 不含**：它是标定侧的输入（由
       `calibration/roc.calibrate_thresholds` 记录在产物里），不是卡的身份——
       混进同一个指纹，"只改了提示词版本"与"改了卡内容"就分不开了。
    2. **`goal` 含，但它不参与当前判定**——这是一处**未言明的宽容，现在言明**。
       实核（v2.3.7）：`DirectionCone.from_samples` 只把 goal 存为元数据，
       `axis` 仅由 `core_samples` 的嵌入均值推导，goal **不进任何向量运算**；
       它真正被用到的地方是 `core/sample_builder`（生成样本时的提示词）与
       `core/return_protocol`（返回文本的 `direction` 字段）。
       也就是说：判据写的是"参与判定的字段"，而实现收了 goal，二者不完全对齐。
       仍收它的理由——goal 是这张卡的**语义身份**（人看卡先看它），
       且未来若让 goal 参与判定（如作为软先验），指纹必须已经跟着动过。
       代价：改目标文案会改指纹，哪怕判定行为一字未变。**这个代价是明知而付的。**

    接受 `RulePattern` 实例或等价的字典，便于无 pydantic 对象的调用方（如把卡
    常量内嵌在脚本里的检测页）复用同一份实现。
    """
    payload = {
        "card_id": card_id,
        "goal": goal,
        "core_samples": _norm_seq(core_samples),
        "boundary_samples": _norm_seq(boundary_samples),
        "negative_samples": _norm_seq(negative_samples),
        "constraints": _norm_seq(constraints),
        "rule_layer": _norm_seq(rule_layer),
        "min_suspect_len": int(min_suspect_len),
        # 等价值归一：`None` 与 `""` 判定等价（见 docstring 规范化规则②）。
        # 用一个 falsy 表达式而非显式 `if`，是为了让"还有没有别的等价值漏掉"
        # 这个问题在代码里显形——目前只有 format_whitelist 一个 str 型可选字段。
        "format_whitelist": (format_whitelist or None),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return f"{FINGERPRINT_VERSION}:{digest}"

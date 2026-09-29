# -*- coding: utf-8 -*-
"""场景卡：引擎与场景的接口层（v2.2.3 固化）。

架构分工（"建卡四问"，见 README 第六节）：引擎只管"怎么查"，规则内容全由
场景卡声明。一张卡 = 目标/核心/边界/反面样本 + 约束条款 + 规则层声明 + 阈值。
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
import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from .core.direction_cone import DirectionCone


class RulePattern(BaseModel):
    name: str
    pattern: str


class ScenarioCard(BaseModel):
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

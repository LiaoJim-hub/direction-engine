# -*- coding: utf-8 -*-
"""输出性质标注层（指根之手）：无数据不输出 → 有效推论（零依赖，纯标准库）

出处：《无数据不输出与有效推论：工程化落地的分层方法论》

分层：

| 层 | 原则 | 本文档的角色 |
|---|---|---|
| 根层 | 无数据不输出 | 不编造、不虚构、不把推论当事实 |
| 显化层 | 有效推论 | 在数据有限时做可辨认、可返回、可修正的推论 |
| 指根之手 | 标明性质 | **本模块**：给每个输出挂性质标注 |

"无数据不输出"禁止的是**编造**，不是**推论**。过度执行会让系统在现实
世界里无法运行（客服 Agent 说"我没有数据，不能回答"）。本模块给出的是
"推论可以输出、但必须标明性质"的最小工程载体。

与检测链的关系（正交）：检测链判定"输出对场景卡的符合性"，
本模块标注"这条输出本身是什么性质"（事实 / 推论 / 假设）。两者可组合，
但互不依赖——本模块不 import 引擎任何其他部分。

三条红线（不可违背，来自原文 4.2）：
  1. 推论不能替代事实 —— output_type 必须是 inference/hypothesis 时，
     verification_path 不得为空；
  2. 推论不能替代决定 —— 本模块没有"执行/采取动作"的任何字段与出口；
  3. 推论不能替代返回 —— return_path 缺失即越界，由 validate() 显形。

与引擎 `confidence` 的划界（防混用）：
  - 引擎 `drift_detector` 的 `confidence` = **判定强度**（光锥位置 + 是否标定）；
  - 本模块 `Provenance.confidence` = **证据强度**（支撑这条输出的数据够不够）。
  两者含义不同，不得互相赋值或覆写。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 输出性质：事实 / 推论 / 假设（原文 2.3、表 4）
# none = 无输出内容（L5"无数据不输出"的专用性质，是根层的沉默，不是故障）
OUTPUT_TYPES = ("fact", "inference", "hypothesis", "none")
# 置信度：证据强度（原文表 4）
CONFIDENCE_LEVELS = ("high", "medium", "low")
# 数据来源（原文表 4 示例）
DATA_SOURCES = ("直接数据", "结构推演", "条件推论", "开放假设")

# 数据分级 L1–L5（原文表 5）：级别决定"能不能直出"
LEVELS: Dict[str, Dict[str, str]] = {
    "L1": {"定义": "有明确数据来源", "输出方式": "可以直接输出，标注来源"},
    "L2": {"定义": "从已知结构推演", "输出方式": "标注“这是结构推论”，附推演链"},
    "L3": {"定义": "给定假设推演", "输出方式": "标注“这是条件推论”，附假设"},
    "L4": {"定义": "提出可检验假设", "输出方式": "标注“这是开放假设”，附检验方式"},
    "L5": {"定义": "没有数据来源", "输出方式": "输出“无数据”，不编造"},
}

# 级别 → 允许的输出性质（L5 无数据，唯一合法性质是 none）
LEVEL_ALLOWED_TYPES: Dict[str, tuple] = {
    "L1": ("fact",),
    "L2": ("inference",),
    "L3": ("inference",),
    "L4": ("hypothesis",),
    "L5": ("none",),
}


@dataclass
class Provenance:
    """输出性质标注（表 4 五字段 + 表 5 数据分级）。

    这不是"美化输出"的元数据，而是"推论可以输出"这一许可的对价：
    凡推论，必须能被辨认、能被返回、能被检验。
    """

    output: str = ""                      # 输出内容本身
    output_type: str = "fact"             # fact / inference / hypothesis
    confidence: str = "low"               # 证据强度：high / medium / low
    data_source: str = ""                 # 直接数据 / 结构推演 / 条件推论 / 开放假设
    inference_chain: str = ""             # 推演链（inference 时应有）
    level: str = "L5"                     # 数据分级：L1–L5
    verification_path: str = ""           # 检验程序：如何验证这个输出
    return_path: str = ""                 # 返回路径：如何回到原始数据
    pending_verification: bool = False    # 数据不足/低置信 → 待核实（原文表 6）
    extra: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "output": self.output,
            "output_type": self.output_type,
            "confidence": self.confidence,
            "data_source": self.data_source,
            "inference_chain": self.inference_chain,
            "level": self.level,
            "verification_path": self.verification_path,
            "return_path": self.return_path,
            "pending_verification": self.pending_verification,
            "extra": self.extra,
        }


def no_data(return_path: str = "") -> Provenance:
    """L5：无数据。输出"无数据"，不编造——这是根层的沉默，不是系统的故障。"""
    return Provenance(output="无数据", output_type="none", confidence="low",
                      data_source="", level="L5",
                      pending_verification=False, return_path=return_path)


def infer_from_structure(output: str, chain: str, verification_path: str,
                         return_path: str, confidence: str = "medium") -> Provenance:
    """L2：结构推演——从已知结构出发推演可能形态，必须附推演链。"""
    return Provenance(output=output, output_type="inference", confidence=confidence,
                      data_source="结构推演", inference_chain=chain,
                      level="L2", verification_path=verification_path,
                      return_path=return_path,
                      pending_verification=(confidence == "low"))


def infer_from_assumption(output: str, assumption: str, verification_path: str,
                          return_path: str, confidence: str = "medium") -> Provenance:
    """L3：条件推论——给定假设推演后果；假设写在 inference_chain 里。"""
    return Provenance(output=output, output_type="inference", confidence=confidence,
                      data_source="条件推论", inference_chain=f"假设：{assumption}",
                      level="L3", verification_path=verification_path,
                      return_path=return_path,
                      pending_verification=(confidence == "low"))


def open_hypothesis(output: str, verification_path: str,
                    return_path: str) -> Provenance:
    """L4：开放假设——可检验的方向，必须附检验方式。"""
    return Provenance(output=output, output_type="hypothesis", confidence="low",
                      data_source="开放假设", level="L4",
                      verification_path=verification_path, return_path=return_path,
                      pending_verification=True)


# ---------------------------------------------------------------------------
# 校验：三条红线（失败只显形，不修正输出内容）
# ---------------------------------------------------------------------------

def validate(p: Provenance) -> Dict:
    """检查一条输出的性质标注是否越界。

    语义与检测链一致：**只显形**——返回违规清单供人或上层流程决定，
    本模块绝不改写 output、绝不代执行、绝不自动补齐路径。
    """
    violations: List[Dict] = []

    def bad(code: str, note: str) -> None:
        violations.append({"code": code, "note": note})

    if p.output_type not in OUTPUT_TYPES:
        bad("E_TYPE", f"output_type 非法：{p.output_type}（须为 {'/'.join(OUTPUT_TYPES)}）")
    if p.confidence not in CONFIDENCE_LEVELS:
        bad("E_CONF", f"confidence 非法：{p.confidence}（须为 {'/'.join(CONFIDENCE_LEVELS)}）")
    if p.level not in LEVELS:
        bad("E_LEVEL", f"level 非法：{p.level}（须为 L1–L5）")

    # 红线一：推论不能替代事实——推论/假设必须指明检验程序
    if p.output_type in ("inference", "hypothesis") and not p.verification_path.strip():
        bad("R1_FACT", "推论/假设未给检验程序（verification_path）——"
                       "推论不能替代事实，必须可检验")
    # 推演链：L2/L3 必须有，否则推论不可辨认
    if p.level in ("L2", "L3") and not p.inference_chain.strip():
        bad("R1_CHAIN", f"{p.level} 缺少推演链（inference_chain）——推论不可辨认")
    # 红线三：推论不能替代返回
    if not p.return_path.strip():
        bad("R3_RETURN", "缺少返回路径（return_path）——推论不能替代返回")
    # 级别与性质匹配
    if p.level in LEVEL_ALLOWED_TYPES and \
            p.output_type not in LEVEL_ALLOWED_TYPES[p.level]:
        bad("E_LEVEL_TYPE",
            f"{p.level}（{LEVELS[p.level]['定义']}）不允许 output_type={p.output_type}")

    # 待核实标注：低置信应显式标待核实（原文表 6 "数据不足时"一格）
    suggested_pending = p.confidence == "low" and p.level != "L5"
    if suggested_pending and not p.pending_verification:
        bad("W_PENDING", "confidence=low 但未标注待核实（pending_verification）")

    # L5 只能是"无数据"：既不能改标 fact 冒充事实，也不能挂推论
    if p.level == "L5" and p.output_type != "none":
        bad("E_L5", "L5（无数据）的 output_type 必须是 none——"
                    "无数据时只输出“无数据”，不冒充事实、不做推论")

    return {
        "valid": not violations,
        "violations": violations,
        "action_hint": "pass" if not violations else "surface",
        "is_manifestation": True,   # 标注本身也是显化
    }


# ---------------------------------------------------------------------------
# 自测：python -m direction_drift.provenance 直接运行
# ---------------------------------------------------------------------------

def _selftest() -> None:
    cases = [
        ("L1 直接数据", Provenance("订单已发货", "fact", "high", "直接数据",
                                   level="L1", return_path="order_db"), True),
        ("L2 结构推论", infer_from_structure("用户可能希望预订酒店", "问'有空房吗'→预订意图",
                                             "询问用户是否希望预订", "原始对话记录"), True),
        ("L3 条件推论", infer_from_assumption("若旺季则需提前两周订房", "当前为旺季",
                                              "查询房价日历", "原始对话记录"), True),
        ("L4 开放假设", open_hypothesis("值得检验：用户偏好或与时段相关",
                                        "A/B 对比不同时段推荐", "原始对话记录"), True),
        ("L5 无数据", no_data("order_db"), True),
        ("越界：推论无检验程序", Provenance("用户想买", "inference", "medium", "结构推演",
                                            inference_chain="x", level="L2",
                                            return_path="db"), False),
        ("越界：推论无返回路径", Provenance("用户想买", "inference", "medium", "结构推演",
                                            inference_chain="x", level="L2",
                                            verification_path="问用户"), False),
        ("越界：低置信未标待核实", Provenance("用户想买", "inference", "low", "结构推演",
                                              inference_chain="x", level="L2",
                                              verification_path="问用户",
                                              return_path="db"), False),
        ("越界：L5 却给了事实性输出", Provenance("大概明天到", "fact", "low", "",
                                                level="L5", return_path="db"), False),
    ]
    all_pass = True
    for name, p, expect in cases:
        r = validate(p)
        ok = r["valid"] == expect
        all_pass = all_pass and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: valid={r['valid']}")
        for v in r["violations"]:
            print(f"        {v['code']}：{v['note']}")
    print("自测结果：" + ("全部通过" if all_pass else "存在未通过用例"))


if __name__ == "__main__":
    _selftest()

"""对齐计算。分数体系已改，阈值必须经 calibrate_thresholds 标定后使用。

量程特性（v2.3.2 显式化，接入前必读）
------------------------------------
`constraint_satisfaction` 只罚不奖：无约束或无违规时恒为 0，违规才递减；
`negative_similarity` 是减项。因此 `overall_alignment` 的**理论上界**
= `权重["cone_alignment"] × 1`，与另外两项无关。

默认权重 0.4/0.4/0.2 的上界是 **0.4**，而 `DriftDetector` 的默认 `high=0.6`。
两者放在一起是不自洽的：任何输出（包括满分在轨句）都够不到 high，
`confirmed_drift` 永不触发，同时分数整体贴着 `low=0.4` 的边界。

这不是"默认值不够精确"，而是**权重与阈值必须来自同一份标定**：
权重改变会整体移动分数分布，使原阈值失效——权重同样是标定产物，
只是它目前没有被 `calibrate_thresholds` 覆盖（该校准只扫阈值）。
仓内默认值只服务于"未标定"与单测；真实接入必须同时提供标定后的权重与阈值，
并用 `check_range_consistency()` 确认二者同源。

空输出与零向量（v2.3.3）
------------------------
`compute()` 对空串 / 纯空白 / 编码器返回零向量**不产生分数**，改返回
`{"overall_alignment": None, "invalid": True, "reason": ...}`。原因见 `compute()`
docstring：nan 被 `min(1.0, nan)` 夹成 1.0，会把 Agent 最常见的真实故障
（空回复、静默失败）记成满分。聚合分数的循环必须先看 `invalid`。
"""
from typing import Dict, Optional

import numpy as np

from .constraint_checker import ConstraintChecker
from .direction_cone import ZERO_NORM_EPS, DirectionCone

DEFAULT_WEIGHTS = {"cone_alignment": 0.4, "constraint_satisfaction": 0.4,
                   "negative_similarity": 0.2}
"""占位权重，待标定。**不要**为了消除下面的量程矛盾而调大这里的数值——
已转正的标定（阈值是在本组权重下扫出来的）会随之整体作废。
要消除矛盾，请显式传入该场景标定出的权重，或改用与权重同源的阈值。"""


def overall_ceiling(weights: Optional[Dict[str, float]] = None) -> float:
    """当前权重下 `overall_alignment` 的理论上界（诊断用，不改任何判定）。

    约束项只罚不奖、负相似度是减项，故上界只看 `cone_alignment` 的权重。
    """
    w = DEFAULT_WEIGHTS if weights is None else weights
    return max(0.0, min(1.0, float(w.get("cone_alignment", 0.0))))


def check_range_consistency(weights: Optional[Dict[str, float]] = None,
                            high: float = 0.6, low: float = 0.4,
                            tol: float = 1e-9) -> Dict:
    """量程自洽性检查：权重算出的上界与判定阈值是否同源（**只显形，不修正**）。

    `consistent=False` 不等于数值有 bug，而是"权重与阈值不同源"——
    典型成因：直接用默认权重配默认阈值，或换了权重却没重新标定。
    返回 {"ceiling", "high", "low", "consistent", "reasons"}。
    """
    ceiling = overall_ceiling(weights)
    reasons = []
    if ceiling + tol < high:
        reasons.append(f"理论上界 {ceiling:.3f} < high={high:.3f}："
                       "confirmed_drift 永远不可能触发")
    if abs(ceiling - low) <= 0.05:
        reasons.append(f"理论上界 {ceiling:.3f} ≈ low={low:.3f}："
                       "满分输出也贴着低分界，正常输出会被大量判为偏低")
    if weights is not None:
        total = float(sum(weights.values()))
        if abs(total - 1.0) > 0.05:
            reasons.append(f"权重之和 {total:.3f} 偏离 1.0，量程会随权重和漂移")
    return {"ceiling": ceiling, "high": float(high), "low": float(low),
            "consistent": not reasons, "reasons": reasons}


class AlignmentCalculator:
    def __init__(self, encoder=None, constraint_checker=None):
        if encoder is None:
            from ..utils.encoder import get_encoder
            encoder = get_encoder()
        self.encoder = encoder
        self.constraint_checker = constraint_checker or ConstraintChecker()

    def compute(self, cone: DirectionCone, output: str,
                weights: Optional[Dict[str, float]] = None) -> Dict:
        """算一条输出的对齐分。

        **空输出是一个性质问题，不是一个分数问题**（v2.3.3 修复）：
        空串 / 纯空白 / 编码器返回零向量时，本方法**不产生任何分数**，
        而是返回 `{"overall_alignment": None, "invalid": True, ...}`。
        理由是零向量归一化会产生 nan，而 `max(0.0, min(1.0, nan))` 在
        Python 里会得到 **1.0** —— 空回复（Agent 最常见的真实故障）
        会被静默记成"满分对齐"。

        调用方纪律：**不要**直接把 `overall_alignment` 当数字用，
        先看 `invalid`；任何在列表里聚合分数的循环都应当跳过 invalid 项
        （`DriftDetector.judge` 已内置这一处置，产出 `no_output` 级）。
        """
        if weights is None:
            weights = DEFAULT_WEIGHTS       # 占位权重；量程陷阱见模块 docstring
        text = output if isinstance(output, str) else ("" if output is None else str(output))
        stripped = text.strip()
        out = self.encoder.encode(text)
        out_norm = float(np.linalg.norm(out))
        if not stripped or not np.isfinite(out_norm) or out_norm < ZERO_NORM_EPS:
            return {"cone_alignment": None, "constraint_satisfaction": 0.0,
                    "constraint_violations": [], "negative_similarity": None,
                    "overall_alignment": None, "cone_position": "unknown",
                    "angle": None, "text_len": len(stripped),
                    "invalid": True,
                    "reason": "empty_output" if not stripped else "zero_vector_embedding"}
        out_normed = out / out_norm
        cone_embs = cone.get_text_embeddings(self.encoder)

        axis_norm = cone.axis / np.linalg.norm(cone.axis)
        cone_alignment = float(np.dot(axis_norm, out_normed))

        c_res = self.constraint_checker.check(text, cone.constraints)
        neg_sim = 0.0
        if cone_embs["negatives"]:
            sims = []
            for n in cone_embs["negatives"]:
                n_norm = float(np.linalg.norm(n))
                if not np.isfinite(n_norm) or n_norm < ZERO_NORM_EPS:
                    continue                      # 退化反面样本不参与（不给 nan）
                sims.append(float(np.dot(n / n_norm, out_normed)))
            neg_sim = max(sims) if sims else 0.0
        cone_result = cone.contains(out)

        overall = (weights["cone_alignment"] * cone_alignment
                   + weights["constraint_satisfaction"] * c_res["satisfaction"]
                   - weights["negative_similarity"] * neg_sim)
        overall = max(0.0, min(1.0, overall))

        return {"cone_alignment": cone_alignment,
                "constraint_satisfaction": c_res["satisfaction"],
                "constraint_violations": c_res["violations"],
                "negative_similarity": neg_sim,
                "overall_alignment": overall,
                "cone_position": cone_result["position"],
                "angle": cone_result["angle"],
                "text_len": len(stripped)}

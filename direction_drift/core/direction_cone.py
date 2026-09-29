"""方向光锥：由方向性样本分布定义，非目标文本单点嵌入"""

import warnings
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

import numpy as np

MIN_CORE_SAMPLES = 20
MIN_NEGATIVE_SAMPLES = 10

# 锥轴数值守卫（v2.3.2）：核心样本方向若彼此抵消（球面平均趋零），
# 归一化会把噪声放大成任意方向，锥轴失去意义 → 拒绝建锥。
MIN_AXIS_NORM = 1e-8     # 硬门：低于此值视为退化
AXIS_NORM_WARN = 0.3     # 软门：低于此值方向一致性偏低，警告但不阻断

# 零范数守卫（v2.3.3）：任何"归一化一个可能为零的向量"的地方都必须先查范数。
# 空串/纯空白经真实编码器（bge 等）返回全零向量，除零得 nan；而 nan 在
# `max(0.0, min(1.0, nan))` 里会被夹成 **1.0**（nan 的比较恒为 False）——
# 即"空输出"被静默记成满分。这是全仓最危险的失效模式，故统一用一个常量收口。
ZERO_NORM_EPS = 1e-12


@dataclass
class DirectionCone:
    axis: np.ndarray                 # 核心样本嵌入均值（归一化）
    aperture: float                  # 核心样本与轴夹角的95分位
    softness: float                  # 边界样本夹角的标准差（下限0.05）
    goal: str = ""
    constraints: List[Union[str, dict]] = field(default_factory=list)
    values: List[str] = field(default_factory=list)
    negative_examples: List[str] = field(default_factory=list)
    core_sample_count: int = 0
    boundary_sample_count: int = 0
    negative_sample_count: int = 0
    frozen: bool = False
    frozen_at: Optional[datetime] = None
    freeze_reason: str = ""
    rebuild_count: int = 0
    last_rebuild_time: Optional[datetime] = None

    @classmethod
    def from_samples(cls, goal, core_samples, boundary_samples=None,
                     negative_samples=None, constraints=None, values=None,
                     embed_fn=None) -> "DirectionCone":
        if len(core_samples) < MIN_CORE_SAMPLES:
            raise ValueError(
                f"核心样本不足：需要至少 {MIN_CORE_SAMPLES} 个，当前 {len(core_samples)} 个。"
                f"可使用 SampleBuilder 的 LLM 合成本功能。")
        if negative_samples and len(negative_samples) < MIN_NEGATIVE_SAMPLES:
            raise ValueError(f"反面样本不足：需要至少 {MIN_NEGATIVE_SAMPLES} 个，"
                             f"当前 {len(negative_samples)} 个。")
        if embed_fn is None:
            from ..utils.encoder import get_encoder
            embed_fn = get_encoder().encode

        core_embs = np.array([embed_fn(s) for s in core_samples])
        _norms = np.linalg.norm(core_embs, axis=1, keepdims=True)
        if float(np.min(_norms)) < MIN_AXIS_NORM:
            raise ValueError(
                "存在零向量核心样本：嵌入函数对部分样本返回了零向量，无法归一化。"
                "请检查 embed_fn（embedding 退化时静默产生 nan 比报错危险得多）。")
        core_embs = core_embs / _norms
        axis = np.mean(core_embs, axis=0)
        axis_norm = float(np.linalg.norm(axis))
        if axis_norm < MIN_AXIS_NORM:
            raise ValueError(
                f"核心样本方向退化：归一化嵌入均值的范数为 {axis_norm:.2e}，接近 0。"
                "核心样本方向互相抵消（球面平均趋零），锥轴无意义。"
                "请检查核心样本是否确实属于同一个业务方向。")
        if axis_norm < AXIS_NORM_WARN:
            warnings.warn(
                f"核心样本方向一致性偏低（均值范数 {axis_norm:.3f}）：锥轴可能不稳定，"
                "建议检查核心样本是否混杂了多个方向。")
        axis = axis / axis_norm

        angles = np.arccos(np.clip(core_embs @ axis, -1, 1))
        b_angles = None
        boundary_count = 0
        if boundary_samples:
            b_embs = np.array([embed_fn(s) for s in boundary_samples])
            b_norms = np.linalg.norm(b_embs, axis=1, keepdims=True)
            if float(np.min(b_norms)) < ZERO_NORM_EPS:
                raise ValueError(
                    "存在零向量边界样本：嵌入函数对部分边界样本返回了零向量"
                    "（空串或编码器退化）。请先清理样本再建锥。")
            b_embs = b_embs / b_norms
            b_angles = np.arccos(np.clip(b_embs @ axis, -1, 1))
            boundary_count = len(boundary_samples)

        # 标定单一来源（v2.1：from_samples 调 calibrate_aperture）
        from ..calibration.roc import calibrate_aperture
        calib = calibrate_aperture(angles, b_angles)
        aperture, softness = calib["aperture"], calib["softness"]
        if b_angles is not None and float(np.mean(b_angles)) > aperture:
            warnings.warn("边界样本平均夹角大于 aperture——aperture 可能收得过窄")

        return cls(axis=axis, aperture=aperture, softness=softness,
                   goal=goal, constraints=constraints or [],
                   values=values or [],
                   negative_examples=list(negative_samples) if negative_samples else [],
                   core_sample_count=len(core_samples),
                   boundary_sample_count=boundary_count,
                   negative_sample_count=len(negative_samples) if negative_samples else 0)

    def get_text_embeddings(self, encoder) -> Dict:
        """锥侧文本嵌入缓存：挂在锥实例上，rebuild_count 变更即失效（修复 id() 复用脏缓存）。"""
        cache = getattr(self, "_text_emb_cache", None)
        if cache is None or cache.get("_version") != self.rebuild_count:
            cache = {
                "values": [encoder.encode(v) for v in self.values],
                "negatives": [encoder.encode(n) for n in self.negative_examples],
                "_version": self.rebuild_count,
            }
            self._text_emb_cache = cache
        return cache

    def rebuild_from_samples(self, core_samples, boundary_samples=None,
                             negative_samples=None, embed_fn=None):
        """重建光锥：仅由返回层/人工触发，用新样本重新推导。不存在对单条输出的 EMA 更新。"""
        new_cone = DirectionCone.from_samples(
            goal=self.goal, core_samples=core_samples,
            boundary_samples=boundary_samples, negative_samples=negative_samples,
            constraints=self.constraints, values=self.values, embed_fn=embed_fn)
        self.axis, self.aperture, self.softness = new_cone.axis, new_cone.aperture, new_cone.softness
        if negative_samples:                       # v2.1 P0-3：反面样本真正写入
            self.negative_examples = list(negative_samples)
        self.core_sample_count = new_cone.core_sample_count
        self.boundary_sample_count = new_cone.boundary_sample_count
        self.negative_sample_count = new_cone.negative_sample_count
        self.rebuild_count += 1                    # 版本号自增 → 缓存自动失效
        self.last_rebuild_time = datetime.now()

    def contains(self, output_embedding: np.ndarray) -> Dict:
        """判断输出嵌入落在光锥的哪一档（core / boundary / outside）。

        v2.3.3：零向量（空输出、纯空白、编码器退化）**拒绝判定**——
        此处不能返回"某种位置"，因为归一化一个零向量得到的是 nan，
        nan 会一路传染到对齐分并最终被夹成满分。fail-closed。
        """
        norm = float(np.linalg.norm(output_embedding))
        if not np.isfinite(norm) or norm < ZERO_NORM_EPS:
            raise ValueError(
                "零向量嵌入：无法判断光锥位置（空输出或编码器退化）。"
                "请在调用前过滤空输出，或先调用 AlignmentCalculator.compute"
                "（它对这种情况返回 invalid 结果而不是抛错）。")
        output_embedding = output_embedding / norm
        axis = self.axis / np.linalg.norm(self.axis)
        cos = float(np.clip(np.dot(axis, output_embedding), -1, 1))
        angle = float(np.arccos(cos))
        if angle < self.aperture - self.softness:
            return {"inside": True, "position": "core", "angle": angle, "cosine": cos}
        if angle < self.aperture + self.softness:
            return {"inside": True, "position": "boundary", "angle": angle, "cosine": cos}
        return {"inside": False, "position": "outside", "angle": angle, "cosine": cos}

    def freeze(self, reason: str):
        self.frozen, self.frozen_at, self.freeze_reason = True, datetime.now(), reason

    def unfreeze(self):
        self.frozen, self.frozen_at, self.freeze_reason = False, None, ""

    def to_dict(self) -> Dict:
        return {"axis": self.axis.tolist(), "aperture": self.aperture,
                "softness": self.softness, "goal": self.goal,
                "constraints": self.constraints, "values": self.values,
                "negative_examples": self.negative_examples,
                "core_sample_count": self.core_sample_count,
                "boundary_sample_count": self.boundary_sample_count,
                "negative_sample_count": self.negative_sample_count,
                "frozen": self.frozen, "rebuild_count": self.rebuild_count,
                "freeze_reason": self.freeze_reason,
                # v2.2.4：补上此前漏掉的两个时间戳——恢复后「何时冻结 / 何时重建」
                # 是审计链的一部分，丢了会让 frozen=True 的锥看不出冻结时点。
                "frozen_at": self.frozen_at.isoformat(timespec="seconds") if self.frozen_at else None,
                "last_rebuild_time": (self.last_rebuild_time.isoformat(timespec="seconds")
                                      if self.last_rebuild_time else None)}

    @classmethod
    def from_dict(cls, data: Dict) -> "DirectionCone":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in data.copy().items() if k in known}
        if isinstance(data.get("axis"), list):
            data["axis"] = np.array(data["axis"])
        for ts_key in ("frozen_at", "last_rebuild_time"):   # ISO 字符串 → datetime
            if isinstance(data.get(ts_key), str):
                try:
                    data[ts_key] = datetime.fromisoformat(data[ts_key])
                except ValueError:
                    data.pop(ts_key)                        # 坏时间戳不阻断恢复
        return cls(**data)

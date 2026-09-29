"""返回协议：可停、可看、可再选。诊断阈值收进 DiagnoseConfig（P2-4）。

状态可序列化（v2.2.4）：`state`（running/observing/paused/returning）与
`diag`（诊断阈值配置）整体存续。**注意纪律**：恢复一个处于 paused/returning
的快照**不会**自动恢复运行——返回与恢复是两个方向的动作，只有人（或显式调用
`resume()`）能让它转回 running。序列化只搬运状态。
"""

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from ..utils.json_safe import jsonable
from .direction_cone import DirectionCone
from .drift_detector import DriftDetector

# 快照格式版本，规则同 DriftDetector.STATE_SCHEMA_VERSION
STATE_SCHEMA_VERSION = 1
_KNOWN_STATES = ("running", "observing", "paused", "returning")


@dataclass
class DiagnoseConfig:
    cone_align_low: float = 0.4        # 未标定参考值
    neg_sim_high: float = 0.7
    slope_drop: float = -0.05
    low_ratio_high: float = 0.6
    calibrated: bool = False


class ReturnProtocol:
    def __init__(self, diag: Optional[DiagnoseConfig] = None):
        self.state = "running"
        self.diag = diag or DiagnoseConfig()

    def act(self, drift_result: Dict, cone: Optional[DirectionCone] = None,
            alignment: Optional[Dict] = None) -> Dict:
        level = drift_result["drift_level"]

        if level == "insufficient_history":
            return {"action": "continue",
                    "message": f"历史不足（{drift_result['history_size']}/{drift_result['needed']}），继续收集"}
        # v2.3.3：输入本身不足以判定（空输出 / 过短）——不判定，也别据此停
        if level == "no_output":
            return {"action": "observe",
                    "message": "空输出/零向量：无分数可判，未进入窗口。这是输入侧问题，"
                               "请先修输入（Agent 静默失败本身就是故障），不要改阈值",
                    "reason": drift_result.get("reason")}
        if level == "too_short":
            return {"action": "observe",
                    "message": f"输入过短（{drift_result.get('text_len')} 字 < "
                               f"{drift_result.get('min_judge_len')} 字）：只显形不判定",
                    "text_len": drift_result.get("text_len")}
        if level == "uncalibrated":
            return {"action": "observe",
                    "message": "未标定观察期，仅记录",
                    "diagnosis_calibrated": self.diag.calibrated}
        if level == "normal":
            self.state = "running"
            return {"action": "continue", "message": "方向正常"}

        reasons = self._diagnose(drift_result, alignment)

        if level == "warning":
            self.state = "observing"
            return {"action": "observe", "message": "对齐度下降，请检查当前输出是否仍指向目标",
                    "reasons": reasons, "diagnosis_calibrated": self.diag.calibrated}
        if level == "drift":
            self.state = "paused"
            return {"action": "pause", "message": "检测到方向漂移，暂停生成",
                    "direction": cone.goal if cone else "", "reasons": reasons,
                    "mean_score": drift_result.get("mean_score"),
                    "diagnosis_calibrated": self.diag.calibrated}
        if level == "confirmed_drift":
            self.state = "returning"
            # v2.3.5：`frozen` 要报**实际做了什么**，不是"这条分支该做什么"。
            # 此前这里硬编码 `"frozen": True`，而 `cone` 为 None 时 `freeze()`
            # 根本没被调用——响应于是在陈述一件没发生的事。同一条字典里
            # `"direction": cone.goal if cone else ""` 已经处理了 None 分支，
            # 说明 None 本来就被考虑过，只是这一处漏了。
            # message 同理不能无条件宣称"冻结方向"：无锥可冻结时要改口，
            # 否则只读 message 的调用方会以为方向已停（见 CONSTITUTION 一.4
            # "只显形不修正"的对偶——**也不许显形一个不存在的动作**）。
            frozen = cone is not None
            if frozen:
                cone.freeze("confirmed_drift")
            return {"action": "return",
                    "message": ("漂移确认，已冻结方向，等待外部观察" if frozen
                                else "漂移确认，未冻结方向（无方向锥可冻结），"
                                     "等待外部观察"),
                    "frozen": frozen, "requires_human": True,
                    "direction": cone.goal if cone else "", "reasons": reasons,
                    "diagnosis_calibrated": self.diag.calibrated}
        return {"action": "continue"}

    def _diagnose(self, drift_result: Dict, alignment: Optional[Dict]) -> List[str]:
        d = self.diag
        reasons: List[str] = []
        if alignment:
            # v2.3.3：invalid 输入下这两项是 None（无分数），不能直接比较
            ca = alignment.get("cone_alignment")
            if isinstance(ca, (int, float)) and ca < d.cone_align_low:
                reasons.append(f"光锥对齐低（{ca:.2f}）")
            for v in alignment.get("constraint_violations", []):
                if v.get("layer") != "none":
                    reasons.append(f"约束违反：{v['constraint']}（{v['layer']}层）")
            ns = alignment.get("negative_similarity")
            if isinstance(ns, (int, float)) and ns > d.neg_sim_high:
                reasons.append(f"与反面案例相似度高（{ns:.2f}）")
        if drift_result.get("slope", 0.0) < d.slope_drop:
            reasons.append(f"对齐度持续下降（斜率{drift_result['slope']:.3f}）")
        if drift_result.get("low_ratio", 0.0) >= d.low_ratio_high:
            reasons.append(f"低分比例高（{drift_result['low_ratio']:.0%}）")
        if drift_result.get("cusum_alarm"):
            reasons.append("CUSUM：小幅持续偏移累积越界（信号，需人工辨认）")
        return reasons or ["综合对齐度低"]

    def resume(self, cone: DirectionCone, detector: DriftDetector) -> Dict:
        cone.unfreeze()
        detector.reset()                     # P0：恢复必须重置（且历史已归档）
        self.state = "running"
        return {"action": "continue", "message": "已恢复，detector已重置（历史已归档）"}

    def revise(self, cone: DirectionCone, detector: DriftDetector,
               core_samples: list, boundary_samples: list = None,
               negative_samples: list = None) -> Dict:
        cone.unfreeze()
        cone.rebuild_from_samples(core_samples, boundary_samples, negative_samples)
        detector.reset()
        self.state = "running"
        return {"action": "continue", "message": "方向已用新样本重建"}

    # ---- v2.2.4 状态序列化（托管持久化用）----

    def to_dict(self) -> Dict:
        return {"schema_version": STATE_SCHEMA_VERSION,
                "state": self.state,
                "diag": jsonable(asdict(self.diag))}

    @classmethod
    def from_dict(cls, data: Dict) -> "ReturnProtocol":
        if not isinstance(data, dict):
            raise TypeError(f"ReturnProtocol.from_dict 需要 dict，得到 {type(data).__name__}")
        sv = data.get("schema_version", STATE_SCHEMA_VERSION)
        if isinstance(sv, int) and sv > STATE_SCHEMA_VERSION:
            raise ValueError(
                f"快照 schema_version={sv} 高于当前引擎支持的 {STATE_SCHEMA_VERSION}，"
                f"请升级 direction-drift 后再恢复。")
        diag_src = data.get("diag") if isinstance(data.get("diag"), dict) else {}
        known = set(DiagnoseConfig.__dataclass_fields__)
        obj = cls(diag=DiagnoseConfig(**{k: v for k, v in diag_src.items() if k in known}))
        state = data.get("state")
        # 未知 state 回落到 running：宁可当它没停过（人工会看到轨迹），
        # 也不能凭一个不认识的字段值直接进入观察/返回态
        obj.state = state if state in _KNOWN_STATES else "running"
        return obj

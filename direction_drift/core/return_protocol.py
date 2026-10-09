"""返回协议：可停、可看、可再选。诊断阈值收进 DiagnoseConfig（P2-4）。

状态可序列化（v2.2.4）：`state`（running/observing/paused/returning）与
`diag`（诊断阈值配置）整体存续。**注意纪律**：恢复一个处于 paused/returning
的快照**不会**自动恢复运行——返回与恢复是两个方向的动作，只有人（或显式调用
`resume()`）能让它转回 running。序列化只搬运状态。

v2.5.3 两条（外部挑刺 P0-1 与一次实测）：
- **`normal` 分支的守卫**：此前它无条件 `self.state = "running"`，于是**一条 normal
  就把 paused / returning 静默推回运行**——与本模块上面那句声明直接矛盾。
  现改为：只有本就在 running 时才 continue；从停态来的一律 `observe` + `requires_human`。
- **`resume()` 全成败**：它原先三步（`cone.unfreeze()` → `detector.reset()` →
  `self.state = "running"`），**任一步抛就留下半恢复态** —— 而"该不该回去"正是这条线
  的价值所在，故返回这件事**不许半途而废**。现改为快照→试→失败回滚，并抛
  `ResumeFailed`；两种结局都记进 `returns`（第一次落痕）。
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List, Optional

from ..utils.json_safe import jsonable
from .direction_cone import DirectionCone
from .drift_detector import DriftDetector

# 快照格式版本，规则同 DriftDetector.STATE_SCHEMA_VERSION
STATE_SCHEMA_VERSION = 1
_KNOWN_STATES = ("running", "observing", "paused", "returning")


class ResumeFailed(RuntimeError):
    """`resume()` 全成败：失败时**什么都没变**，且这件事可被读出来。

    为什么要有这个类型（而不是让底层异常直接冒上来）：`resume()` 是三步
    （`cone.unfreeze()` → `detector.reset()` → `state = "running"`）。
    任一底步失败时，若只把原异常抛出去，调用方看不出"系统停在哪" ——
    而"要不要回去"正是这条线的价值所在（见模块 docstring 与 CONSTITUTION 一.4
    "只显形不修正"的对偶：**也不许把一次失败显形成一次成功**）。

    - `state_kept`：失败后被保持住的协议状态（即失败前那个）。
    - `cause`：底层异常的类型名与消息（原文保留，不外泄成一句笼统话）。
    """

    def __init__(self, state_kept: str, cause: BaseException):
        self.state_kept = state_kept
        self.cause = cause
        super().__init__(
            "返回失败（{0}: {1}）—— **什么都没改**，本协议仍停在 `{2}`。"
            "请查因后重试 `resume()`；★ 不要把它读成已恢复。".format(
                type(cause).__name__, cause, state_kept))


class ReviseFailed(RuntimeError):
    """`revise()` 全成败：失败时**锥、检测器、协议状态三样一样都没动**。

    ★ v2.5.3 修（实测）：原实现把"解冻"排在"重建"之前 —— 重建一抛，锥就停在
    「已解冻但没重建」，即**一次什么都没发生、却解了冻**。而"方向"是这个引擎的核心物，
    它不该在失败时处于"半新"状态。

    - `state_kept`：失败后被保持住的协议状态。
    - `cause`：底层异常（原文保留）。
    """

    def __init__(self, state_kept: str, cause: BaseException):
        self.state_kept = state_kept
        self.cause = cause
        super().__init__(
            "重建方向失败（{0}: {1}）—— **锥、检测器、协议状态三样都没动**，"
            "本协议仍停在 `{2}`，方向仍是旧的那一个。"
            "请查因后重试 `revise()`；★ 不要把它读成方向已换。".format(
                type(cause).__name__, cause, state_kept))


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
        # ★ v2.5.3：返回动作的落痕（第一次）。append-only，不删除。
        #   为什么要有它：本模块声称"返回是人的动作"，而**此前没有任何地方记下
        #   "谁在什么时候返回了"** ⇒ "返回过"这件事不可溯，而这条线的规则二正是"可溯"。
        #   ★ 两种结局都记：成功与失败。失败不记 ⇒ 会把"返回失败"整段抹掉。
        self.returns: List[Dict] = []

    def return_log(self) -> List[Dict]:
        """返回动作的落痕（只读副本）。成功与失败都在里面。"""
        return [dict(r) for r in self.returns]

    def _log_return(self, ok: bool, cone: Optional[DirectionCone],
                    cause: Optional[BaseException] = None,
                    trigger: str = "resume()") -> None:
        rec = {"at": datetime.now().isoformat(timespec="seconds"),
               "ok": bool(ok),
               "state_before": self.state,
               "state_after": "running" if ok else self.state,
               "cone_was_frozen": bool(cone is not None and cone.frozen),
               "trigger": trigger}
        if cause is not None:
            rec["error"] = "{0}: {1}".format(type(cause).__name__, cause)
        self.returns.append(rec)

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
            # ★★ 守卫（2026-10-09）：本模块声明「只有人（或显式调用 resume()）能让它转回
            # running」。本分支原先无条件 self.state = "running" ⇒ 一条 normal 就把
            # paused / returning 静默推回运行（已实测复现）。
            # ⇒ 故：只有【本就在 running】时才维持 running；从停态来的，不自动回。
            if self.state != "running":
                return {"action": "observe",
                        "message": ("输入未再触发漂移，但**返回是人的动作**，不自动恢复 —— "
                                    "请显式调用 resume()（继续）或 revise()（用新样本重建方向）"),
                        "state_kept": self.state,
                        "requires_human": True}
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
        """显式返回：**全成败**。要么三样（锥 / 检测器 / 协议状态）都回 running，要么一样都不动。

        ★ v2.5.3 修（实测复现）：此前是三行顺序执行，**任一步抛就留下半恢复态** ——
        例如 `detector.reset()` 抛之后，检测器已被重置、锥已解冻，而协议仍写着 `paused`。
        那正是这条线修过四次的同一形状：**状态与实况不符**（可见的 metadata 在陈述一件
        没发生的事）。而"该不该回去"恰是这条线的价值所在 ⇒ 返回不许半途而废。

        实现：★ 先取快照（回滚面**精确列出**，不用反射）⇒ 逐件试 ⇒ 任一失败**回滚并抛
        `ResumeFailed`**。两种结局都落进 `returns`。

        ★ 一条要写明的耦合：回滚面与 `DriftDetector.reset()` 的实现绑定
        （`archive` / `history` / `means` / `_cusum_s` / `state`）。
        若 reset 改了它动哪些字段，这里必须跟着改——**否则回滚会悄悄回不干净**。
        """
        snap = {
            "protocol_state": self.state,
            "cone": (cone.frozen, cone.frozen_at, cone.freeze_reason),
            "det_archive_len": len(detector.archive),
            "det_history": list(detector.history),
            "det_means": list(detector.means),
            "det_cusum": detector._cusum_s,
            "det_state": detector.state,
        }
        was_frozen = cone.frozen
        try:
            cone.unfreeze()
            detector.reset()                 # P0：恢复必须重置（且历史已归档）
            self.state = "running"
        except BaseException as e:           # noqa: BLE001  ★ 连 KeyboardInterrupt 也要回滚
            self._rollback(cone, detector, snap)
            self._log_return(False, cone, e)
            raise ResumeFailed(snap["protocol_state"], e) from e
        self._log_return(True, cone)
        return {"action": "continue",
                "message": "已恢复，detector已重置（历史已归档）",
                "cone_was_frozen": was_frozen}

    @staticmethod
    def _restore_seq(dst, src: list) -> None:
        """把 `dst` 恢复成源快照 `src`（内容与原顺序）。

        ★ 为什么不能直接 `dst[:] = src`：`DriftDetector.history` 与 `.means`
        是 **`deque(maxlen=…)`**，切片赋值在 deque 上不成立（`TypeError`）。
        用 `clear()` + `extend()` 对两者都成立，且**不动 maxlen**。
        """
        dst.clear()
        dst.extend(src)

    @staticmethod
    def _rollback(cone: DirectionCone, detector: DriftDetector, snap: Dict) -> None:
        """把三样都按快照还原。★ 回滚面与 `DriftDetector.reset()` 绑定（见 `resume()` docstring）。"""
        cone.frozen, cone.frozen_at, cone.freeze_reason = snap["cone"]
        del detector.archive[snap["det_archive_len"]:]      # 去掉 reset 追加的那一条
        ReturnProtocol._restore_seq(detector.history, snap["det_history"])
        ReturnProtocol._restore_seq(detector.means, snap["det_means"])
        detector._cusum_s = snap["det_cusum"]
        detector.state = snap["det_state"]

    def revise(self, cone: DirectionCone, detector: DriftDetector,
               core_samples: list, boundary_samples: list = None,
               negative_samples: list = None, embed_fn=None) -> Dict:
        """用新样本重建方向：**全成败**（★ 丙案 · 两段式）。

        ★ v2.5.3 修（实测）：原实现是三行顺序执行，且**"解冻"跑在"重建"之前** ——
        于是 `rebuild_from_samples()` 一抛，锥就停在「**已解冻但没重建**」：
        一次"什么都没发生，却解了冻"。而 `rebuild_from_samples()` 自身**是两段式**
        （先 `DirectionCone.from_samples(...)` 造成新锥，全成才逐个赋值）⇒
        ★ **只要把不可失败的赋值留到最后，就能做到全成败 —— 不需要造第二只锥。**

        故本方法把顺序改为：**① 重建（会抛）→ ② 重置检测器（会抛）→ ③ 提交**。
        ①② 任一抛 ⇒ **回滚并抛 `ReviseFailed`**；两种结局都落进 `returns`。

        ★ 回滚面与 `DirectionCone.rebuild_from_samples()` 和
        `DriftDetector.reset()` 的实现绑定（见下方 `_rollback_revise`）——
        它们若改了动哪些字段，这里必须跟着改。
        """
        snap = {
            "protocol_state": self.state,
            "cone": (cone.frozen, cone.frozen_at, cone.freeze_reason),
            "cone_axis": cone.axis, "cone_aperture": cone.aperture,
            "cone_softness": cone.softness,
            "cone_neg": list(cone.negative_examples),
            "cone_counts": (cone.core_sample_count, cone.boundary_sample_count,
                            cone.negative_sample_count),
            "cone_rebuild_count": cone.rebuild_count,
            "cone_last_rebuild": cone.last_rebuild_time,
            "det_archive_len": len(detector.archive),
            "det_history": list(detector.history),
            "det_means": list(detector.means),
            "det_cusum": detector._cusum_s,
            "det_state": detector.state,
        }
        was_frozen = cone.frozen
        try:
            # ★ 顺序是刻意的：会抛的两件事都排在"提交"之前
            cone.rebuild_from_samples(core_samples, boundary_samples, negative_samples,
                                      embed_fn=embed_fn)
            detector.reset()                 # P0：换方向必须重置
            cone.unfreeze()                  # ← 提交段：以下都不会抛
            self.state = "running"
        except BaseException as e:           # noqa: BLE001
            self._rollback_revise(cone, detector, snap)
            self._log_return(False, cone, e, trigger="revise()")
            raise ReviseFailed(snap["protocol_state"], e) from e
        self._log_return(True, cone, trigger="revise()")
        return {"action": "continue", "message": "方向已用新样本重建",
                "cone_was_frozen": was_frozen}

    @staticmethod
    def _rollback_revise(cone: DirectionCone, detector: DriftDetector, snap: Dict) -> None:
        """把锥与检测器都按快照还原。★ 回滚面与那两个实现绑定（见 `revise()` docstring）。"""
        cone.frozen, cone.frozen_at, cone.freeze_reason = snap["cone"]
        cone.axis, cone.aperture, cone.softness = (snap["cone_axis"], snap["cone_aperture"],
                                                   snap["cone_softness"])
        cone.negative_examples = snap["cone_neg"]
        (cone.core_sample_count, cone.boundary_sample_count,
         cone.negative_sample_count) = snap["cone_counts"]
        cone.rebuild_count = snap["cone_rebuild_count"]
        cone.last_rebuild_time = snap["cone_last_rebuild"]
        del detector.archive[snap["det_archive_len"]:]
        ReturnProtocol._restore_seq(detector.history, snap["det_history"])
        ReturnProtocol._restore_seq(detector.means, snap["det_means"])
        detector._cusum_s = snap["det_cusum"]
        detector.state = snap["det_state"]

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

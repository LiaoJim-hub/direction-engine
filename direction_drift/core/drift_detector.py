"""漂移判断。核心纪律：未标定不判定（P0-1）。

v2.3.3 起多加一层：**输入不足也不判定**。空输出/零向量 → `no_output`，
长度 < `min_judge_len` → `too_short`；两者都不进入窗口、不改状态。
原因见 `DEFAULT_MIN_JUDGE_LEN` 与 `judge()` 的 docstring。

状态可序列化（v2.2.4）：`to_dict()/from_dict()` 覆盖**配置 + 全部运行态**
（history 滚动窗口 / means 斜率窗口 / archive 审计归档 / CUSUM 累积量 / state）。
托管层把会话存 Redis 后重启恢复，窗口不归零、判定不失真——这正是此前
`DriftDetector` 只有进程内状态、`get_state()` 只回只读视图所缺的那一块。
纪律不变：序列化只搬运状态，不改阈值、不改判定、不触发任何动作。
"""

from collections import deque
from datetime import datetime, timezone
from typing import Dict

import numpy as np

from ..utils.json_safe import jsonable

# 快照格式版本：字段增删时自增。加载到**更高**版本的快照会明确报错，
# 而不是按旧字段白名单悄悄解析（误读一个未来格式，比拒绝它危险得多）。
# v2（2.3.3）：config 新增 min_judge_len。旧快照（v1）缺少该键 → 用默认值。
STATE_SCHEMA_VERSION = 2

# 判定链的长度下限（v2.3.3）：短于此长度的输入**不判定**，只显形。
#
# 与场景卡的 `min_suspect_len` 是两件事，别混：
# - 卡的 min_suspect_len（默认 12）是**场景策略**——"短文本不算疑似"，
#   属卡（规则属场景）；
# - 这里的 min_judge_len 是**认识论下限**——"几个字的输入在语义空间里
#   不携带可测方向"，属引擎（与"未标定不判定"同源）。
#
# 为何必须存在：长度保护原先**只装在卡路径**（ScenarioCard.is_suspect），
# 而 drift_level 由本类单独算出，"好的/收到/嗯嗯"这类最常见合法短回复
# 在窗口填满后可以直接推到 confirmed_drift → return → 冻结光锥。
# 默认 4 只挡掉 1–3 字的寒暄与确认（实测复现的就是 2 字那批）；
# 真实场景请把卡的 min_suspect_len 传进来。
DEFAULT_MIN_JUDGE_LEN = 4

# 构造参数白名单：from_dict 只从这些键里取配置，未知键一律忽略（向前兼容）
_CONFIG_FIELDS = ("window", "high", "low", "drift_ratio", "slope_warning",
                  "calibrated", "archive_limit", "cusum_target",
                  "cusum_k", "cusum_h", "min_judge_len")


class DriftDetector:
    def __init__(self, window=5, high=0.6, low=0.4, drift_ratio=0.6,
                 slope_warning=-0.05, calibrated: bool = False,
                 archive_limit: int = 20,
                 cusum_target: float = None, cusum_k: float = 0.05,
                 cusum_h: float = 0.3,
                 min_judge_len: int = DEFAULT_MIN_JUDGE_LEN):
        self.window = window
        self.high, self.low = high, low              # 占位值；必须经标定回填
        self.drift_ratio = drift_ratio
        self.slope_warning = slope_warning
        self.calibrated = calibrated                 # False 时只记录不判定
        self.min_judge_len = int(min_judge_len)      # 短于此长度不判定（见模块常量）
        self.history = deque(maxlen=50)
        self.archive: list = []                      # 审计归档：reset 时移入，不删除
        self.archive_limit = archive_limit
        self.means = deque(maxlen=20)                # 滚动均值序列（斜率输入）
        self.state = "warmup"
        # v2.2.2 CUSUM（修正版）：one-sided 下偏累积。与《科学映射与优化》9.3
        # 示例的两点关键差异——max(0,…) 下限防止负累积，告警即复位防止永久误报。
        # 哲学门控约束：CUSUM 只提供信号（normal→warning 提级），绝不替代
        # drift/confirmed_drift 的阈值判定，更不触发任何自动返回动作。
        self.cusum_target = cusum_target             # None → 用 self.high 代理受控均值
        self.cusum_k = cusum_k                       # 允许量（slack）
        self.cusum_h = cusum_h                       # 告警阈值
        self._cusum_s = 0.0

    def judge(self, alignment: Dict) -> Dict:
        """判一条对齐结果。

        v2.3.3 起在判定前先做**可判定性前置检查**，两条都属于"输入本身不足
        以判定"，与"未标定不判定"同源，纪律一致：

        - `invalid`（空输出 / 零向量）→ `no_output`；
        - 长度 < `min_judge_len` → `too_short`。

        两者都**不进入滑动窗口、不改任何状态、不触发任何动作**——
        理由：短句/空句的分数是任意的（分数低不等于方向漂），把它喂进窗口
        会污染均值、斜率与 CUSUM，进而把一个纯格式问题升级成"确认漂移"。
        调用方看到这两个级别应当去修输入，而不是去改阈值。
        """
        if alignment.get("invalid"):
            return {"drift_level": "no_output",
                    "score": None, "history_size": len(self.history),
                    "reason": alignment.get("reason", "invalid_input"),
                    "pending_verification": True,
                    "note": "空输出/零向量：无分数可判，未进入窗口（这不是 0 分，也不是满分）"}

        text_len = alignment.get("text_len")
        if text_len is not None and text_len < self.min_judge_len:
            return {"drift_level": "too_short",
                    "score": alignment.get("overall_alignment"),
                    "text_len": text_len, "min_judge_len": self.min_judge_len,
                    "history_size": len(self.history),
                    "pending_verification": True,
                    "note": "输入过短，语义上不携带可测方向：只显形，不判定、不进入窗口。"
                            "真实场景应把场景卡的 min_suspect_len 传入本类构造参数"}

        score = alignment["overall_alignment"]
        pos = alignment.get("cone_position", "core")
        self.history.append({"score": score, "position": pos,
                             "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})

        if len(self.history) < self.window:
            return {"drift_level": "insufficient_history", "score": score,
                    "history_size": len(self.history), "needed": self.window,
                    "pending_verification": True}
        self.state = "running"

        recent = [h["score"] for h in list(self.history)[-self.window:]]
        mean_score = float(np.mean(recent))
        low_ratio = sum(1 for s in recent if s < self.low) / len(recent)
        self.means.append(mean_score)
        slope = (float(np.polyfit(range(len(self.means)), list(self.means), 1)[0])
                 if len(self.means) >= 3 else 0.0)

        # v2.2.2 CUSUM 累积（仅标定后运行；告警即复位）
        cusum_alarm = False
        if self.calibrated:
            target = self.cusum_target if self.cusum_target is not None else self.high
            self._cusum_s = max(0.0, self._cusum_s + (target - score) - self.cusum_k)
            if self._cusum_s > self.cusum_h:
                cusum_alarm = True
                self._cusum_s = 0.0

        base = {"score": score, "mean_score": mean_score, "slope": slope,
                "low_ratio": low_ratio, "cone_position": pos,
                "cusum_s": self._cusum_s, "cusum_alarm": cusum_alarm,
                "confidence": self._confidence(pos),
                "pending_verification": self._pending_verification(pos),
                "history_size": len(self.history)}

        if not self.calibrated:
            return {"drift_level": "uncalibrated", **base,
                    "note": "阈值未标定，仅记录轨迹。跑 calibrate_thresholds 后置 calibrated=True"}

        level = "normal"
        if mean_score < self.low and low_ratio >= self.drift_ratio:
            level = "confirmed_drift"
        elif mean_score < self.low or (low_ratio >= self.drift_ratio
                                       and slope < self.slope_warning):
            level = "drift"
        elif mean_score < self.high or slope < self.slope_warning:
            level = "warning"

        # 光锥位置降级为诊断信号：只把 normal 提到 warning，绝不越级
        if level == "normal" and pos in ("outside", "boundary"):
            level = "warning"
        # v2.2.2 CUSUM 同样只提级不越级：小幅持续偏移累积越界 → 提示观察
        if cusum_alarm and level == "normal":
            level = "warning"
        return {"drift_level": level, **base}

    def _confidence(self, pos: str) -> str:
        """v2.2.2 精度加权（诊断标注）：标定状态 + 光锥位置 + 样本量 → 判定
        强度的置信标签。只调节信息呈现，不替代任何阈值判定，不转移决定权。"""
        if not self.calibrated or pos == "outside":
            return "low"
        if pos == "boundary":
            return "medium"
        return "high" if len(self.history) >= 2 * self.window else "medium"

    def _pending_verification(self, pos: str) -> bool:
        """待核实标注（《无数据不输出与有效推论》表 6「数据不足时」一格）。

        低置信（未标定，或输出落在光锥外）时，判定应当标为待核实——
        这是"有效推论"的对价：结论可以输出，但必须显式标明它还没被核实。
        纪律不变：**这是一个标注，不是动作**——它不改变 drift_level、
        不触发冻结、不阻断开流程，决定权仍完全在人。
        注意与 `confidence` 的划界：这里的 low 指**判定强度**，
        不是 `provenance.Provenance.confidence` 的**证据强度**。
        """
        return not self.calibrated or pos == "outside"

    def get_state(self) -> Dict:
        return {"history": list(self.history), "archive": self.archive,
                "state": self.state, "calibrated": self.calibrated}

    def reset(self):
        """恢复用：历史归档而非清空（补充二）。"""
        if self.history:
            self.archive.append(list(self.history))
            if len(self.archive) > self.archive_limit:
                self.archive.pop(0)
        self.history.clear()
        self.means.clear()
        self._cusum_s = 0.0                          # v2.2.2：CUSUM 状态随 reset 归零
        self.state = "warmup"

    # ---- v2.2.4 状态序列化（托管持久化用）----

    def to_dict(self) -> Dict:
        """完整快照：配置与运行态分开，便于恢复时比对配置是否漂移。

        - `config`：window/high/low/… 构造参数（含标定回填的阈值）
        - `runtime`：history / means / archive / state / cusum_s
        返回纯 JSON 可序列化对象（list/float/str/bool/None），无 numpy 类型。
        """
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "config": jsonable({k: getattr(self, k) for k in _CONFIG_FIELDS}),
            "runtime": {
                "history": jsonable(list(self.history)),
                "means": [float(m) for m in self.means],
                "archive": jsonable(self.archive),
                "state": self.state,
                "cusum_s": float(self._cusum_s),
            },
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DriftDetector":
        """从快照恢复。兼容两种输入：

        1. `to_dict()` 产出的分组格式（`config` / `runtime`）；
        2. 扁平格式（键直接就是构造参数，运行态同名键同层）——便于手写或旧存根。

        容错纪律：未知键忽略、缺失项用默认值；但 `schema_version` **高于**当前支持时
        抛 ValueError（拒绝静默误读未来格式）。
        """
        if not isinstance(data, dict):
            raise TypeError(f"DriftDetector.from_dict 需要 dict，得到 {type(data).__name__}")
        sv = data.get("schema_version", STATE_SCHEMA_VERSION)
        if isinstance(sv, int) and sv > STATE_SCHEMA_VERSION:
            raise ValueError(
                f"快照 schema_version={sv} 高于当前引擎支持的 {STATE_SCHEMA_VERSION}，"
                f"请升级 direction-drift 后再恢复。")

        cfg_src = data["config"] if isinstance(data.get("config"), dict) else data
        rt_src = data["runtime"] if isinstance(data.get("runtime"), dict) else data
        cfg = {k: cfg_src[k] for k in _CONFIG_FIELDS if k in cfg_src}
        obj = cls(**cfg)

        if int(obj.window) < 1:
            raise ValueError(f"快照 window={obj.window} 非法（必须 ≥1）")
        if int(obj.min_judge_len) < 0:
            raise ValueError(f"快照 min_judge_len={obj.min_judge_len} 非法（必须 ≥0）")

        # deque(maxlen=…) 语义保留：超长自动丢弃最旧，正常快照不该超限
        history = rt_src.get("history")
        if isinstance(history, list):
            obj.history.extend(h for h in history if isinstance(h, dict))
        means = rt_src.get("means")
        if isinstance(means, list):
            obj.means.extend(float(m) for m in means)
        archive = rt_src.get("archive")
        if isinstance(archive, list):
            obj.archive = [[dict(h) for h in batch if isinstance(h, dict)]
                           for batch in archive if isinstance(batch, list)]
            if len(obj.archive) > obj.archive_limit:
                obj.archive = obj.archive[-obj.archive_limit:]
        state = rt_src.get("state")
        if state in ("warmup", "running"):
            obj.state = state
        cusum_s = rt_src.get("cusum_s")
        if isinstance(cusum_s, (int, float)):
            obj._cusum_s = float(cusum_s)
        return obj

    def config_fingerprint(self) -> Dict:
        """配置指纹（不含运行态）：恢复会话时比对，阈值/窗口被改动即暴露。"""
        return {k: getattr(self, k) for k in _CONFIG_FIELDS}

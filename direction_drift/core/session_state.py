"""会话快照：cone + detector + protocol 的整体序列化（托管持久化用，v2.2.4）。

为什么放在引擎里：托管层需要把「一个被检测系统」的完整状态存进 Redis，再在重启后
原样恢复。若由托管层自己拼装三段 to_dict，就出现了第二份状态知识——引擎改字段时
托管层会静默失配。快照结构由引擎定义，托管层只负责存取。

**纪律（与 CONSTITUTION.md 一致）**
- 本模块只搬运状态：不判定、不改阈值、不 freeze/unfreeze。
- `restore()` 出来的锥若处于 frozen（被冻结），**不会**自动解除；恢复检测也不等于
  恢复运行，仍需显式调用 `ReturnProtocol.resume()`。返回动作永远由人触发。
- 快照版本高于当前引擎支持时**抛 ValueError**，绝不按旧字段白名单悄悄解析。

用法::

    from direction_drift.core.session_state import snapshot, restore

    blob = snapshot(cone, detector, protocol)      # 存 Redis（json.dumps 即可）
    parts = restore(blob)                          # 取回
    cone, detector, protocol = parts["cone"], parts["detector"], parts["protocol"]
"""

from typing import Any, Dict

from ..utils.json_safe import jsonable
from .direction_cone import DirectionCone
from .drift_detector import DriftDetector
from .return_protocol import ReturnProtocol

STATE_SCHEMA_VERSION = 1
_SECTIONS = ("cone", "detector", "protocol")


def snapshot(cone: DirectionCone, detector: DriftDetector,
             protocol: ReturnProtocol, extra: Dict = None) -> Dict[str, Any]:
    """打包一个会话的完整状态。返回纯 JSON 可序列化 dict。

    `extra` 供托管层附加自己的元数据（租户、system_id、标定版本等），
    引擎不解释其内容，只在 `restore()` 时原样带回（非 dict 则丢弃）。
    """
    if not isinstance(cone, DirectionCone):
        raise TypeError("cone 必须是 DirectionCone")
    if not isinstance(detector, DriftDetector):
        raise TypeError("detector 必须是 DriftDetector")
    if not isinstance(protocol, ReturnProtocol):
        raise TypeError("protocol 必须是 ReturnProtocol")
    blob = {"schema_version": STATE_SCHEMA_VERSION,
            "cone": cone.to_dict(),
            "detector": detector.to_dict(),
            "protocol": protocol.to_dict()}
    if isinstance(extra, dict):
        blob["extra"] = jsonable(extra)
    return blob


def restore(data: Dict) -> Dict[str, Any]:
    """从快照恢复会话三件套，返回 `{"cone":…, "detector":…, "protocol":…, "extra":…}`。

    任一段缺失或版本过高即抛错——调用方应显式处理（丢弃会话重建 / 提示人工），
    而不是拿到一个"半恢复"的会话继续判定。
    """
    if not isinstance(data, dict):
        raise TypeError(f"session_state.restore 需要 dict，得到 {type(data).__name__}")
    sv = data.get("schema_version", STATE_SCHEMA_VERSION)
    if isinstance(sv, int) and sv > STATE_SCHEMA_VERSION:
        raise ValueError(
            f"会话快照 schema_version={sv} 高于当前引擎支持的 {STATE_SCHEMA_VERSION}，"
            f"请升级 direction-drift 后再恢复。")
    for key in _SECTIONS:
        if not isinstance(data.get(key), dict):
            raise ValueError(f"会话快照缺少「{key}」段，拒绝恢复（不返回半恢复状态）")
    extra = data.get("extra")
    return {"cone": DirectionCone.from_dict(data["cone"]),
            "detector": DriftDetector.from_dict(data["detector"]),
            "protocol": ReturnProtocol.from_dict(data["protocol"]),
            "extra": dict(extra) if isinstance(extra, dict) else {}}

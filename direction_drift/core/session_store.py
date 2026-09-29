# -*- coding: utf-8 -*-
"""会话存储接缝：把"会话状态存在哪个边界上"隔离在一个接口后面（v2.3.6）。

与 `session_state.py` 的分工（**两层，别合并**）
------------------------------------------------
- `session_state` 管**序列化**：cone + detector + protocol ↔ 纯 JSON。
- 本模块管**存取**：谁拿着这些状态、放在进程内还是进程外。

把两者合成一个接口（例如让 store 直接暴露 `snapshot()` / `restore()`）会让
"换存储"顺带改动状态布局——那就出现了第二份状态知识，正是 `session_state`
的模块 docstring 要避免的事。存储层只搬运，**不解释结构**。

"演示"与"持久"的真正区别
------------------------
不是"存不存数据"，而是**会话状态存在哪个边界上**：

| | 进程内（`InMemorySessionStore`） | 进程外（Redis / PG / 磁盘） |
|---|---|---|
| 状态活在 | 当前 Python 进程的内存里 | 进程之外的独立服务或文件 |
| 进程重启 | 会话全部归零 | 从快照恢复，判定轨迹连续 |
| 多实例 | 每个进程各有各的会话（必然不一致） | 可共享同一份状态 |

两种形态下**检测、返回、标定逻辑完全相同**——差别只在 `SessionStore` 的实现。
这就是"接缝"的全部含义：换存储 = 补一个类 + 换一行构造参数，业务层一个字不改。

现状与限度（诚实声明，与 CONSTITUTION 一致）
--------------------------------------------
- 引擎只提供**进程内**实现。引擎不依赖任何外部服务，因此**不提供 Redis/PG
  实现**——托管方子类化 `SessionStore` 即可，这正是本接口存在的理由。
  迁移指引（含可复制的示例骨架）见 `docs/persistence.md`。
- 接口只保证"读写会经过这里"，**不保证读-改-写是原子的**：两个并发请求对同一
  `session_id` 各自 get → 各自改 → 各自 set，后写的覆盖先写的（丢失更新）。
  解决它需要存储层提供 CAS/锁或请求级排队，不属于本接口能承担的事。接口的价值
  是把它**显式化**——在此之前它是隐式的活对象，看不出来。
- 本模块不判定、不改阈值、不 freeze/unfreeze、不 import 检测链的算法；
  存取失败不吞，由调用方显式处理。
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .direction_cone import DirectionCone
from .drift_detector import DriftDetector
from .return_protocol import ReturnProtocol

DEFAULT_MAX_IDLE_SECONDS = 3600
DEFAULT_MAX_COUNT = 500


class Session:
    """一个被检测系统的运行态：方向锥 + 漂移检测器 + 返回协议。

    刻意是可变容器而不是不可变值：这三件东西本来就该就地演化
    （`judge()` 推进滑动窗口、`act()` 迁移返回状态、`rebuild` 改锥），
    会话就是"允许就地演化的那个单位"。
    """

    __slots__ = ("cone", "detector", "protocol")

    def __init__(self, cone: DirectionCone, detector: DriftDetector,
                 protocol: ReturnProtocol):
        for name, obj, cls in (("cone", cone, DirectionCone),
                               ("detector", detector, DriftDetector),
                               ("protocol", protocol, ReturnProtocol)):
            if not isinstance(obj, cls):
                raise TypeError(
                    f"Session.{name} 必须是 {cls.__name__}，"
                    f"得到 {type(obj).__name__}")
        self.cone = cone
        self.detector = detector
        self.protocol = protocol

    def __repr__(self) -> str:
        return (f"Session(goal={self.cone.goal!r}, state={self.protocol.state!r}, "
                f"history={len(self.detector.history)})")


class SessionStore(ABC):
    """会话状态的存取边界。**业务层只允许经这三个方法接触会话。**

    接口只收业务层真正调用的方法，不预支未来的需要（"加了就是猜"）：

    - `snapshot()` / `restore()` **不在这里**——它们属序列化层（`session_state`），
      由具体实现内部调用，不上升为契约；
    - `delete()` **不在这里**——当前没有任何端点需要单独删一个会话；
      它是进程内实现清理自身的工具，留在具体类上，不占契约位。
    """

    @abstractmethod
    def get(self, session_id: str) -> Optional[Session]:
        """取回会话；不存在返回 `None`。

        - `None` 的含义是"没有这个会话"。**实现方不得凭空构造一个**：首次调用
          是业务层必须知道的事实（它决定要不要报 400、要不要建锥）。
        - 取用即视为一次访问：滑动过期所依赖的"最近活跃"在这里刷新。
        - **返回的对象是否与内部分享同一份内存，由实现决定**——调用方不得依赖
          别名语义，改完必须 `set()` 回去。这条约束是"换实现结论不变"的前提：
          内存实现里那次多余的 `set()` 几乎零成本，进程外实现里它就是那次写。
        """

    @abstractmethod
    def set(self, session_id: str, session: Session) -> None:
        """写入整个会话（**整体覆盖**，不做字段级合并）。

        实现方不得做字段级合并推断：会话是可变的，只有业务层知道改了哪几处。
        """

    @abstractmethod
    def sweep(self) -> List[str]:
        """做一次过期/容量清理，返回**本实现能确定**被淘汰的会话 id。

        **空列表 = "不知道有谁被淘汰"，不等于"没有人被淘汰"。** 外部存储把过期
        交给 TTL（如 Redis 的 `EXPIRE`）时返回空列表是正确的；此时调用方不得把
        `evicted` 为空读成"本次无人掉线"——那是在陈述一件自己不知道的事，
        与"未标定不判定"是同一条纪律。
        """


class InMemorySessionStore(SessionStore):
    """进程内实现：一个 dict + 滑动过期 + 容量上限。

    行为与 v2.3.6 之前 `api/server.py` 里的模块级 `sessions` 字典**逐条对齐**
    （做接缝这一步不夹带行为变更，要改就单独提）：

    - `get()` 刷新最近活跃 → 活跃会话不被误删；
    - 空闲超过 `max_idle_seconds` 的会话在 `sweep()` 时移除，**不回报 id**
      （与容量淘汰不同：无人问津的会话被回收不需要通知谁。容量淘汰**必须**回报，
      因为它是"有人可能在用、但被挤掉了"，见 v2.3.3 的 `evicted_sessions`）；
    - 超过 `max_count` 时按最久未使用淘汰，被淘汰的 id 由 `sweep()` 回报。

    两条已知限度（与旧实现相同）：
    - `sweep()` 是 O(n) 全表扫描，且**不会**自己定时跑：本类不启动线程、
      不持有定时器，什么时候清理由调用方决定；
    - 不加锁：并发下同一会话的读-改-写会互相覆盖（见模块 docstring）。

    进程一停，这里的一切归零——这是本实现的定义，不是缺陷。

    `now_fn` 可注入，供测试推进时钟而不必 sleep（默认 `datetime.now`）。
    """

    def __init__(self, max_idle_seconds: int = DEFAULT_MAX_IDLE_SECONDS,
                 max_count: int = DEFAULT_MAX_COUNT,
                 now_fn: Callable[[], datetime] = datetime.now):
        self.max_idle_seconds = int(max_idle_seconds)
        self.max_count = int(max_count)
        self._now = now_fn
        self._sessions: Dict[str, Session] = {}
        self._last_accessed: Dict[str, datetime] = {}

    # ------------------------------------------------------------ 接口三法

    def get(self, session_id: str) -> Optional[Session]:
        session = self._sessions.get(session_id)
        if session is not None:
            self._last_accessed[session_id] = self._now()
        return session

    def set(self, session_id: str, session: Session) -> None:
        if not isinstance(session, Session):
            raise TypeError(f"set() 需要 Session，得到 {type(session).__name__}")
        self._sessions[session_id] = session
        self._last_accessed[session_id] = self._now()

    def sweep(self) -> List[str]:
        now = self._now()
        for sid in [s for s, t in self._last_accessed.items()
                    if (now - t).total_seconds() > self.max_idle_seconds]:
            self.delete(sid)                        # 滑动过期：活跃会话不被误删
        evicted: List[str] = []
        over = len(self._sessions) - self.max_count
        if over > 0:
            oldest = sorted(self._sessions, key=self._last_accessed.__getitem__)
            for sid in oldest[:over]:
                self.delete(sid)
                evicted.append(sid)
        return evicted

    # ------------------------------------------- 具体实现自用的工具，不占契约位

    def delete(self, session_id: str) -> bool:
        """删除一个会话；返回是否真的删掉了。"""
        self._last_accessed.pop(session_id, None)
        return self._sessions.pop(session_id, None) is not None

    def count(self) -> int:
        """当前会话数（自检/运维用；契约不要求外部实现提供）。"""
        return len(self._sessions)

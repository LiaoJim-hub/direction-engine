# 从进程内到进程外：会话持久化怎么换

> 本文回答一个具体问题：**现在跑单进程演示，以后要多实例/持久化，要改多少东西？**
> 结论先给：检测、返回、标定三层**一行都不用改**；要写的是一个新的
> `SessionStore` 子类，外加一行构造参数。

## 一、区别不在"存不存数据"，在"状态存在哪个边界上"

| | 进程内（`InMemorySessionStore`，默认） | 进程外（Redis / PG / 磁盘） |
|---|---|---|
| 状态活在 | 当前 Python 进程的内存里 | 进程之外的独立服务或文件 |
| 进程重启 | 会话全部归零 | 从快照恢复，判定轨迹连续 |
| 多实例 | 每个进程各有各的会话（必然不一致） | 可共享同一份状态 |
| 代价 | 零 | 一次网络往返 / 一份要维护的基础设施 |

`DriftDetector` 有滑动窗口、滚动均值与 CUSUM 累积量——这些都要跨请求存活，
否则每来一条输出都从空窗口开始，"判定"退化成"单条打分"。
**一次请求内部不需要持久化**（请求里的文本序列本身就是一段有序流），
真正需要跨请求存活的只有会话状态本身。这就是"演示"与"持久"的分界。

## 二、接缝是三个方法

```python
from direction_drift.core.session_store import SessionStore

class SessionStore(ABC):
    def get(self, session_id) -> Optional[Session]:   # 不存在返回 None
    def set(self, session_id, session) -> None:       # 整体覆盖
    def sweep(self) -> List[str]:                     # 清理 + 回报能确定的淘汰
```

来源：`direction_drift/core/session_store.py`。接口**只有这三个方法**，这是刻意的：

- **`snapshot()` / `restore()` 不在接口里**。它们属序列化层
  （[`core/session_state.py`](../direction_drift/core/session_state.py)），
  由具体实现内部调用。把它们提升为契约，等于让"换存储"顺带改动状态布局——
  那就出现了第二份状态知识，而引擎改字段时外部实现会**静默失配**。
- **`delete()` 不在接口里**。当前没有任何端点需要单独删一个会话；
  它是进程内实现清理自身的工具。接口不预支未来的需要。

## 三、迁移三步

**第 1 步：写一个子类。** 只需要实现三个方法（下面第五节有 Redis 骨架）。

**第 2 步：传进去。** 业务层一个字不动：

```python
from direction_drift.api.server import create_app

app = create_app(store=MyStore())      # 不传就是 InMemorySessionStore
```

用 `uvicorn` 时把它放进你自己的模块，因为 `--factory` 需要一个无参可调用对象：

```python
# myapi.py
from direction_drift.api.server import create_app
from my_store import MyStore

def app_factory():
    return create_app(store=MyStore())
```

```bash
uvicorn myapi:app_factory --factory --port 8000
```

**第 3 步：跑前哨测试**（第六节）。这一步不能省。

## 四、Redis 骨架（**未随仓测试**，说明性示例）

引擎不依赖任何外部服务，CI 里跑不了 redis，所以**下面这段代码没有测试覆盖**。
它是契约的写法示例，请按自己的环境验证后再上线——三处需要留意的地方已标注。

```python
import json
from typing import List, Optional

from direction_drift.core.session_state import restore, snapshot
from direction_drift.core.session_store import Session, SessionStore


class RedisSessionStore(SessionStore):
    PREFIX = "de:session:"

    def __init__(self, client, ttl_seconds: int = 3600):
        self.redis = client
        self.ttl = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"{self.PREFIX}{session_id}"

    def get(self, session_id: str) -> Optional[Session]:
        raw = self.redis.get(self._key(session_id))
        if raw is None:
            return None
        # ① 取用即续期：滑动过期在这里实现，不指望业务层记得 touch
        self.redis.expire(self._key(session_id), self.ttl)
        parts = restore(json.loads(raw))
        return Session(parts["cone"], parts["detector"], parts["protocol"])

    def set(self, session_id: str, session: Session) -> None:
        # ② 序列化只走引擎的单点实现，不自己拼字段
        blob = snapshot(session.cone, session.detector, session.protocol)
        self.redis.set(self._key(session_id), json.dumps(blob), ex=self.ttl)

    def sweep(self) -> List[str]:
        # ③ 过期由 Redis 负责，我们**不知道**谁刚过期 → 如实返回空列表。
        #    接口写明空列表的含义是"不知道"，不是"没有人被淘汰"。
        return []
```

## 五、迁移时**会**变的东西（诚实清单）

这四条不解决，换存储就是把隐患从"进程内"搬到"分布式"：

1. **`evicted_sessions` 的语义变了。** 进程内实现能报出"谁被挤掉了"；Redis 版
   返回空（TTL 不问自取）。接入方不能再靠这个字段做"会话掉线告警"。要恢复这个
   能力，得自己维护一份活跃集合——那是多一个维护点，不是免费的。
2. **读-改-写不是原子的。** 两个并发请求对同一 `session_id` 各自 get → 各自
   judge → 各自 set，后写的覆盖先写的（丢失更新）。收敛方式三选一：客户端保证
   每会话串行；存储层做 CAS（`snapshot()` 目前不带版本号，需自己加）；或每租户
   独立进程。这不是接口能单独解决的问题，所以它写在接口文档里而**不是**藏在实现里。
3. **崩溃窗口仍然存在。** `set()` 之前进程挂掉，该条输出**不留下判定记录**。
   检测层是"显形"，不是"记账"——它不承诺每条都落盘。要审计完整性请自己写 WAL。
4. **`frozen` 的锥不会自动解冻。** `session_state` 的纪律：恢复**检测**不等于
   恢复**运行**，被冻结的锥仍需显式 `ReturnProtocol.resume()`。返回动作永远由人触发。

另外两条与存储无关、但迁移时一定会碰到：

- **快照版本必须硬校验。** `restore()` 在 `schema_version` 高于当前引擎支持时
  抛 `ValueError`。**不要**用 `try/except` 兜成"重建会话"——那会把"引擎升级导致的
  不兼容"静默降级成"轨迹丢失"，而后者看起来像正常运行。
- **认证与数据隔离不在这里。** `server.py` 只有一个全局 API key，没有租户维度。
  多租户怎么切取决于接入方的形态（SaaS / 私有部署 / 嵌进 Agent 框架各不一样），
  引擎不猜——按 CONSTITUTION 一.2，这类规则住在接入方，不住在引擎。

## 六、怎么确认迁移没走样：跑前哨测试

不要靠"看起来能跑"。用一份**每次都真序列化的 store** 对着同一串输入跑，
逐条比对判定是否完全一致：

```python
# 思路：get 给副本、set 存副本 → 对业务层的要求与 Redis 完全相同
# 完整实现见 tests/test_api_server.py 的 JsonRoundTripStore
texts = ["帮我把第2天的行程排一下", "再考虑一下预算", "", "好的", "我直接帮你写段代码"]
assert _run(InMemorySessionStore(), texts) == _run(JsonRoundTripStore(), texts)
```

它不证明某个 Redis 实现正确，它证明**业务层没有依赖任何只有进程内存储才成立的
性质**。仓内这份测试在 [`tests/test_api_server.py`](../tests/test_api_server.py)，
建议把它复制进你自己的仓——换存储时它是唯一能在 CI 里替你挡住"状态没写回"的东西。

**为什么这条测试不可替代**：进程内实现的 `get()` 返回的是活对象，漏写
`set()` 时一切照常（下一请求拿到的还是那一份），只有换成会 detached 的实现，
"忘了写回"才会表现为"判定轨迹归零"。

# -*- coding: utf-8 -*-
"""会话存储接缝的测试（v2.3.6 新增）。

`SessionStore` 是"演示 → 持久"唯一的改动面，所以它单独成册：接缝本身若没有
测试，换实现时出的问题会在业务层才暴露——那时已经分不清是"接缝漏了"还是
"新实现写错了"。
"""
import pathlib

import pytest

from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.return_protocol import ReturnProtocol
from direction_drift.core.session_store import (InMemorySessionStore, Session,
                                                SessionStore)

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cone(encoder, samples):
    """建一次锥复用：全部用例都用同一份"旅行管家"方向。"""
    return DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"],
        negative_samples=samples["negative"], embed_fn=encoder.encode)


@pytest.fixture()
def make_session(cone):
    def _make():
        return Session(cone=cone, detector=DriftDetector(), protocol=ReturnProtocol())
    return _make


def _clock(start="2026-09-29T10:00:00"):
    """可推进的假时钟（避免 sleep，也避免依赖真实时间）。"""
    from datetime import datetime, timedelta
    state = {"now": datetime.fromisoformat(start)}

    def now():
        return state["now"]

    def advance(seconds):
        state["now"] = state["now"] + timedelta(seconds=seconds)

    return now, advance


# --------------------------------------------------------------- 接口的边界


def test_the_interface_stays_minimal():
    """接口只收业务层真正调用的方法，不预支未来的需要。

    具体守两条：
    - `snapshot()` / `restore()` **不在这里**——它们属序列化层
      （`session_state`）。塞进来就会出现第二份状态布局知识：换存储时
      顺带改了结构，而改结构本该由引擎单点决定。
    - `delete()` **不在这里**——没有任何端点需要单独删一个会话。它是进程内
      实现清理自身的工具，加了就是猜未来。
    """
    assert set(SessionStore.__abstractmethods__) == {"get", "set", "sweep"}


def test_store_module_has_no_third_party_imports():
    """存储层只搬运，不引入计算依赖（与两个零依赖模块同一套架构纪律）。"""
    src = (ROOT / "direction_drift" / "core" / "session_store.py").read_text(
        encoding="utf-8")
    for banned in ("import numpy", "import sklearn", "import jieba",
                   "import torch", "sentence_transformers"):
        assert banned not in src, f"session_store.py 不应出现 {banned!r}"


def test_three_methods_are_all_an_external_store_needs(make_session):
    """接口能否真落地，用最小子类证明——外部实现只需这三个方法。"""
    class DictStore(SessionStore):
        def __init__(self):
            self.data = {}

        def get(self, session_id):
            return self.data.get(session_id)

        def set(self, session_id, session):
            self.data[session_id] = session

        def sweep(self):
            return []          # TTL 由存储层负责 → 不知道谁被淘汰

    s = DictStore()
    assert s.get("nope") is None
    s.set("a", make_session())
    assert s.get("a") is not None
    assert s.sweep() == []


# ----------------------------------------------------------------- 会话容器


@pytest.mark.parametrize("field,value", [
    ("cone", {"not": "a cone"}),
    ("detector", None),
    ("protocol", "nope"),
])
def test_session_rejects_wrong_types(make_session, field, value):
    """三件套类型不对就当场报错——不等到判定时才炸。"""
    kwargs = {"cone": make_session().cone,
              "detector": DriftDetector(), "protocol": ReturnProtocol()}
    kwargs[field] = value
    with pytest.raises(TypeError, match=field):
        Session(**kwargs)


def test_session_repr_is_useful_without_lying(make_session):
    r = repr(make_session())
    assert "Session(" in r and "state='running'" in r and "history=0" in r


# ----------------------------------------------------- 进程内实现：读写与清理


def test_get_missing_returns_none(make_session):
    assert InMemorySessionStore().get("没有这个会话") is None


def test_set_then_get_roundtrip(make_session):
    store = InMemorySessionStore()
    s = make_session()
    store.set("a", s)
    assert store.get("a") is s
    assert store.count() == 1


def test_set_rejects_non_session(make_session):
    with pytest.raises(TypeError, match="Session"):
        InMemorySessionStore().set("a", {"cone": None})


def test_get_refreshes_activity_so_an_active_session_survives(make_session):
    """滑动过期：一直被取用的会话不该被空闲规则回收。"""
    now, advance = _clock()
    store = InMemorySessionStore(max_idle_seconds=3600, now_fn=now)
    store.set("a", make_session())

    advance(3000)                      # 50 分钟，未超 60 分钟
    assert store.get("a") is not None  # 取用即刷新
    advance(3000)                      # 再 50 分钟：距上次取用仅 50 分钟

    assert store.sweep() == []
    assert store.count() == 1, "活跃会话被空闲规则误删了"


def test_idle_sessions_are_collected_and_not_reported(make_session):
    """空闲回收不回报 id——与容量淘汰刻意不同，这条边界要写死。

    容量淘汰必须回报（会话还在被用，是被挤掉的，见 v2.3.3 的
    `evicted_sessions`）；空闲回收不回报（一小时没人问津的会话被收回，
    没有谁需要被通知）。把两者混为一谈会让 `sweep()` 的返回值含义漂移。
    """
    now, advance = _clock()
    store = InMemorySessionStore(max_idle_seconds=3600, now_fn=now)
    store.set("a", make_session())

    advance(3601)
    assert store.sweep() == [], "空闲过期的会话不回报 id"
    assert store.count() == 0, "但它必须真的被回收"


def test_capacity_eviction_reports_the_evicted_ids(make_session):
    """容量淘汰按最久未使用，且**必须**把被淘汰的 id 交出来。"""
    now, advance = _clock()
    store = InMemorySessionStore(max_idle_seconds=10 ** 9, max_count=3, now_fn=now)
    for sid in ("a", "b", "c", "d"):
        store.set(sid, make_session())
        advance(1)                     # 让访问时间有先后

    evicted = store.sweep()
    assert evicted == ["a"], "最久未使用的那一个应被淘汰并回报"
    assert store.count() == 3
    assert store.get("a") is None and store.get("d") is not None


def test_sweep_with_nothing_to_do_returns_empty(make_session):
    store = InMemorySessionStore()
    store.set("a", make_session())
    assert store.sweep() == []
    assert store.count() == 1


def test_delete_reports_whether_it_actually_deleted(make_session):
    store = InMemorySessionStore()
    store.set("a", make_session())
    assert store.delete("a") is True
    assert store.delete("a") is False, "删不存在的会话要如实说没删到"


def test_the_memory_store_does_not_hide_the_detached_requirement(make_session):
    """进程内实现**允许**返回活对象（这是它的性质），但接口不许依赖这件事。

    本用例把这件事写清楚，免得将来有人写"反正拿到的是同一个对象，
    不用 set 回去"——那种代码换成进程外实现就会静默丢状态。
    对应的端到端证据在 `tests/test_api_server.py` 的 JsonRoundTripStore。
    """
    store = InMemorySessionStore()
    s = make_session()
    store.set("a", s)
    got = store.get("a")
    got.detector.history.append({"score": 0.0, "position": "core"})
    assert store.get("a").detector.history, (
        "进程内实现的 get 返回活对象——这是已知性质；接口不保证它，"
        "业务层仍必须写回")

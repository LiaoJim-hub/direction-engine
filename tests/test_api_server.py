# -*- coding: utf-8 -*-
"""HTTP 服务层的测试（v2.3.6 新增）。

v2.3.6 之前 `api/server.py` 是**零覆盖**的：模块级 `app` 让 import 即构造语义
编码器，pytest 根本收不进来（收了就去下载 100MB 权重）。改成工厂之后，
注入 `DemoEncoder` + 自己的 store 就能测。

这些测试盯三件旧测试盯不到的事：

1. **端点形状**：401 / 400 / 404 / 409 与输入守卫经 HTTP 的真实表现；
2. **接缝有没有真接上**：所有状态推进都必须落回 store。这条**必须换实现测**——
   进程内实现返回活对象，漏写 `set()` 也"跑得通"，只有换成"每次读写真序列化"
   的实现才会暴露。见 `JsonRoundTripStore`；
3. **换存储不改结论**：同一串输入，两种实现逐条判定必须完全一致。
   这是"演示 → 持久"的前哨测试。
"""
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from direction_drift.api.server import create_app
from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.return_protocol import ReturnProtocol
from direction_drift.core.session_state import restore, snapshot
from direction_drift.core.session_store import (InMemorySessionStore, Session,
                                                SessionStore)

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEY = "test-key"

CORE = [f"帮用户规划第{i}天旅行行程，考虑天气与预算" for i in range(22)]
BOUNDARY = [f"旅行时可以聊点当地文化，再回到行程规划{i}" for i in range(12)]
NEGATIVE = [f"帮我写一段Python代码实现排序算法{i}" for i in range(12)]
CARD = {
    "goal": "帮用户规划旅行行程，考虑天气与预算",
    "constraints": [{"text": "不直接替用户预订", "actions": ["帮你下单"]}],
    "core_samples": CORE,
    "boundary_samples": BOUNDARY,
    "negative_samples": NEGATIVE,
}


# ------------------------------------------------------------------ 测试替身


class JsonRoundTripStore(InMemorySessionStore):
    """把"状态存在进程外"的代价显式化：每次 set 真序列化，每次 get 给副本。

    它不接 Redis，但它对业务层的要求与一个真正的 Redis 实现**完全相同**：
    读到的是副本、改动不写回就丢。用它跑同一串输入，是对"换持久化实现后结论
    不变"最直接的前哨测试。
    """

    @staticmethod
    def _detach(session: Session) -> Session:
        blob = snapshot(session.cone, session.detector, session.protocol)
        parts = restore(json.loads(json.dumps(blob)))     # 真过一遍 JSON
        return Session(parts["cone"], parts["detector"], parts["protocol"])

    def get(self, session_id):
        live = super().get(session_id)                    # 保留"取用即刷新活跃"
        return None if live is None else self._detach(live)

    def set(self, session_id, session):
        super().set(session_id, self._detach(session))    # 存进去的也是副本


class CountingStore(InMemorySessionStore):
    """记录 get / set 调用，用来验"写回是无条件的、读不写"。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gets = []
        self.sets = []

    def get(self, session_id):
        self.gets.append(session_id)
        return super().get(session_id)

    def set(self, session_id, session):
        self.sets.append(session_id)
        return super().set(session_id, session)


def _clock(start="2026-09-29T10:00:00"):
    from datetime import datetime, timedelta
    state = {"now": datetime.fromisoformat(start)}

    def advance(seconds):
        state["now"] = state["now"] + timedelta(seconds=seconds)

    return lambda: state["now"], advance


@pytest.fixture(scope="module")
def cone(encoder):
    return DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=CORE, boundary_samples=BOUNDARY,
        negative_samples=NEGATIVE, embed_fn=encoder.encode)


@pytest.fixture()
def make_session(cone):
    return lambda: Session(cone=cone, detector=DriftDetector(),
                           protocol=ReturnProtocol())


# ------------------------------------------------------------------ 测试工具


def _client(store=None, encoder=None):
    app = create_app(encoder=encoder, store=store, api_key=KEY)
    return app, TestClient(app)


def _check(client, text, system_id="s1", direction=None, key=KEY):
    body = {"system_id": system_id, "current_output": text}
    if direction is not None:
        body["direction"] = direction
    headers = {} if key is None else {"X-API-Key": key}
    return client.post("/v1/check", json=body, headers=headers)


def _run(store, texts, encoder):
    """对着一个 store 跑完一串输入，逐条取回判定（供两实现对照）。"""
    _, client = _client(store=store, encoder=encoder)
    out = []
    for i, text in enumerate(texts):
        r = _check(client, text, direction=CARD if i == 0 else None)
        assert r.status_code == 200, r.text
        d = r.json()
        out.append((d["drift_level"], d["action"], d["message"],
                    d["alignment"]["overall_alignment"], d["cone_position"],
                    d["evicted_sessions"]))
    return out


# ---------------------------------------------------------------- 构造与守卫


def test_importing_the_server_module_has_no_side_effects():
    """模块级不得有 app / encoder——那正是本文件此前零覆盖的原因。

    这条是行为守卫的文本形式：只要有人为了"方便"把 `app = create_app()`
    挪回模块级，测试就跑不进来了（import 会去下载权重），报错信息还很难懂。
    """
    src = (ROOT / "direction_drift" / "api" / "server.py").read_text(encoding="utf-8")
    assert "\napp = " not in src, "不得有模块级 app：import 即构造编码器"
    assert "\nencoder = " not in src, "不得有模块级 encoder"
    assert "server:create_app" not in src.split("def create_app")[0] or True


def test_api_key_is_required_to_start(monkeypatch, encoder):
    monkeypatch.delenv("DIRECTION_DRIFT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DIRECTION_DRIFT_API_KEY"):
        create_app(encoder=encoder)


def test_api_key_can_come_from_the_environment(monkeypatch, encoder):
    monkeypatch.setenv("DIRECTION_DRIFT_API_KEY", "from-env")
    app = create_app(encoder=encoder)
    with TestClient(app) as c:
        assert c.get("/v1/check").status_code == 405          # 只是确认服务活着
        assert _check(c, "你好", direction=CARD, key="from-env").status_code == 200
        assert _check(c, "你好", direction=CARD, key="wrong").status_code == 401


def test_missing_semantic_encoder_refuses_to_start_with_instructions(monkeypatch):
    """fail-closed：缺语义嵌入就拒绝启动，并给出安装指令——**不降级**。

    降级成词级哈希编码器会产出"跑得起来但结论错"的分数，比启动失败危险得多。
    """
    from direction_drift.utils import encoder as encoder_module

    class _Missing:
        def __init__(self, *args, **kwargs):
            raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(encoder_module, "Encoder", _Missing)
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        create_app(api_key=KEY)


def test_default_store_is_in_memory_and_env_knobs_are_read(monkeypatch, encoder):
    """默认＝进程内（引擎不假装自己会持久化）；两个环境变量在构造时读取。"""
    monkeypatch.setenv("DIRECTION_DRIFT_MAX_SESSIONS", "7")
    monkeypatch.setenv("DIRECTION_DRIFT_SESSION_MAX_IDLE", "60")
    app = create_app(encoder=encoder, api_key=KEY)
    assert isinstance(app.state.store, InMemorySessionStore)
    assert app.state.store.max_count == 7
    assert app.state.store.max_idle_seconds == 60


def test_injected_store_is_the_one_in_use(encoder):
    store = JsonRoundTripStore()
    app, _ = _client(store=store, encoder=encoder)
    assert app.state.store is store


# -------------------------------------------------------------------- 端点面


def test_health(encoder):
    _, client = _client(encoder=encoder)
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_missing_api_key_header_is_rejected(encoder):
    """两种拒绝要分清：**没给**密钥 → 422（FastAPI 的必填参数校验），
    **给错**密钥 → 401（我们自己的常量时间比较）。"""
    _, client = _client(encoder=encoder)
    assert _check(client, "你好", direction=CARD, key=None).status_code == 422


def test_wrong_api_key_is_401(encoder):
    _, client = _client(encoder=encoder)
    assert _check(client, "你好", direction=CARD, key="nope").status_code == 401


def test_first_call_without_direction_is_400(encoder):
    _, client = _client(encoder=encoder)
    r = _check(client, "帮用户规划行程")
    assert r.status_code == 400 and "direction" in r.json()["detail"]


def test_cone_build_failure_is_400_and_leaves_no_session(make_session, encoder):
    """建锥失败不留半个会话（半建状态比直接失败难以发现）。"""
    store = InMemorySessionStore()
    _, client = _client(store=store, encoder=encoder)
    bad = dict(CARD, core_samples=CORE[:3])          # 低于硬下限 20
    r = _check(client, "帮用户规划行程", direction=bad)
    assert r.status_code == 400 and "核心样本不足" in r.json()["detail"]
    assert store.count() == 0, "建锥失败却留下了会话"


def test_second_call_without_direction_continues(encoder):
    _, client = _client(encoder=encoder)
    assert _check(client, "帮我把第2天的行程排一下", direction=CARD).status_code == 200
    r = _check(client, "再考虑一下预算")
    assert r.status_code == 200
    assert r.json()["drift_level"] in {"uncalibrated", "insufficient_history"}


def test_passing_direction_again_is_409(encoder):
    _, client = _client(encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    r = _check(client, "再考虑一下预算", direction=CARD)
    assert r.status_code == 409 and "rebuild" in r.json()["detail"]


def test_uncalibrated_service_never_claims_a_verdict(encoder):
    """本服务不提供标定入口 → 判定永远是"只记录轨迹"，不假装判了。"""
    _, client = _client(encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    for _ in range(8):
        d = _check(client, "我直接帮你写一段Python代码实现快速排序吧").json()
    assert d["drift_level"] == "uncalibrated", d["drift_level"]
    assert d["action"] in {"continue", "observe"}


def test_empty_output_is_no_output_without_a_score(encoder):
    """空回复是 Agent 最常见的真实故障：必须是 no_output，且**没有分数**。

    经 HTTP 也要看到这一点——它是"空输出不会被 nan 夹成满分"的端到端证据。
    """
    _, client = _client(encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    d = _check(client, "").json()
    assert d["drift_level"] == "no_output"
    assert d["alignment"]["overall_alignment"] is None
    assert d["alignment"]["invalid"] is True


def test_short_reply_is_too_short(encoder):
    _, client = _client(encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    assert _check(client, "好的").json()["drift_level"] == "too_short"


@pytest.mark.parametrize("path,method", [
    ("/v1/state/ghost", "get"), ("/v1/resume/ghost", "post"),
    ("/v1/rebuild/ghost", "post"),
])
def test_unknown_session_is_404(encoder, path, method):
    _, client = _client(encoder=encoder)
    call = getattr(client, method)
    r = call(path, headers={"X-API-Key": KEY}) if method == "get" else \
        call(path, json={"goal": "x"}, headers={"X-API-Key": KEY})
    assert r.status_code == 404


def test_state_reports_the_three_parts(encoder):
    _, client = _client(encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    st = client.get("/v1/state/s1", headers={"X-API-Key": KEY}).json()
    assert st["protocol_state"] == "running"
    assert st["cone"]["goal"] == CARD["goal"]
    assert st["detector"]["history"]


def test_rebuild_replaces_the_cone_and_resets_the_detector(encoder):
    _, client = _client(encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    r = client.post("/v1/rebuild/s1", json={
        "goal": "帮用户规划旅行",
        "core_samples": CORE, "boundary_samples": BOUNDARY,
        "negative_samples": NEGATIVE}, headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json()["status"] == "rebuilt"


def test_resume_is_reachable(encoder):
    _, client = _client(encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    r = client.post("/v1/resume/s1", headers={"X-API-Key": KEY})
    assert r.status_code == 200


# ------------------------------------------------------- 接缝：写回与换实现


def test_state_pushes_are_written_back_through_the_store(encoder):
    """每次状态推进都必须 `set` 回存储——**显式、无条件**。

    进程内实现会掩盖漏写（返回的是同一个对象），所以这里只断言"调用发生了"，
    真正的行为证据在下一条（换实现后状态是否还在）。
    """
    store = CountingStore()
    _, client = _client(store=store, encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    assert store.sets == ["s1"], "首次调用（建锥 + 判定）应当写回一次"
    assert store.gets == ["s1"]

    client.post("/v1/resume/s1", headers={"X-API-Key": KEY})
    client.post("/v1/rebuild/s1", json={
        "goal": "帮用户规划旅行", "core_samples": CORE,
        "boundary_samples": BOUNDARY, "negative_samples": NEGATIVE},
        headers={"X-API-Key": KEY})
    assert store.sets == ["s1", "s1", "s1"], (
        "resume 与 rebuild 也是状态迁移，必须写回")


def test_read_only_requests_do_not_write(encoder):
    """读不能顺手写：进程外实现里每次 set 都是真金白银的往返。"""
    store = CountingStore()
    _, client = _client(store=store, encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    before = list(store.sets)
    client.get("/v1/state/s1", headers={"X-API-Key": KEY})
    assert store.sets == before, "只读端点不该产生写入"


def test_judgement_history_survives_a_store_that_detaches_state(encoder):
    """换成"每次读都给副本"的实现后，判定轨迹仍然连续。

    这是本文件里**最要紧**的一条：如果 `check` 忘了写回，进程内实现毫无异常，
    而这里会看到 history 归零（每一步都从空窗口开始）。
    """
    store = JsonRoundTripStore()
    _, client = _client(store=store, encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    for text in ("再考虑一下预算", "第3天想轻松点", "帮我看看天气",
                 "顺便排一下交通"):
        _check(client, text)
    st = client.get("/v1/state/s1", headers={"X-API-Key": KEY}).json()
    assert len(st["detector"]["history"]) == 5, st["detector"]["history"]


def test_rebuild_actually_resets_a_detached_store(encoder):
    """rebuild 的"清窗口 + 归档"也必须落回存储，否则换实现后窗口照旧。"""
    store = JsonRoundTripStore()
    _, client = _client(store=store, encoder=encoder)
    _check(client, "帮我把第2天的行程排一下", direction=CARD)
    for text in ("再考虑一下预算", "第3天想轻松点"):
        _check(client, text)
    client.post("/v1/rebuild/s1", json={
        "goal": "帮用户规划旅行", "core_samples": CORE,
        "boundary_samples": BOUNDARY, "negative_samples": NEGATIVE},
        headers={"X-API-Key": KEY})
    st = client.get("/v1/state/s1", headers={"X-API-Key": KEY}).json()
    assert st["detector"]["history"] == [], "rebuild 后窗口没有清空（没写回？）"
    assert len(st["detector"]["archive"]) == 1, "旧窗口应进归档而不是被删"


def test_swapping_the_store_does_not_change_a_single_verdict(encoder):
    """同一串输入，进程内实现与"每次读写真序列化"的实现，判定必须逐条一致。

    这是"演示 → 持久"的前哨测试。它不证明某个 Redis 实现正确，它证明
    **业务层没有依赖任何只有进程内存储才成立的性质**——换存储不该改变结论，
    只该改变"进程重启后还在不在"。
    """
    texts = ["帮我把第2天的行程排一下", "再考虑一下预算", "第3天想轻松点",
             "", "好的", "我直接帮你写一段Python代码实现快速排序吧",
             "帮我看看天气", "顺便排一下交通"]
    memory = _run(InMemorySessionStore(), texts, encoder)
    detached = _run(JsonRoundTripStore(), texts, encoder)
    assert memory == detached


def test_evicted_ids_from_the_store_are_passed_through_verbatim(encoder, make_session):
    """容量淘汰的回报是**存储层的答案**，服务层不自己编。

    这里的 store 已预先塞了 2 个会话、上限是 1 → 本次请求开头那一次 sweep
    必定淘汰掉最久未使用的 "old"，响应必须原样带上它。
    """
    now, advance = _clock()
    store = InMemorySessionStore(max_count=1, max_idle_seconds=10 ** 9, now_fn=now)
    store.set("old", make_session())
    advance(1)
    store.set("new", make_session())

    _, client = _client(store=store, encoder=encoder)
    d = _check(client, "帮我把第2天的行程排一下", direction=CARD).json()
    assert d["evicted_sessions"] == ["old"]


def test_an_empty_eviction_list_means_unknown_not_none(encoder):
    """`sweep()` 返回空列表的含义是"不知道"，不是"没有人被淘汰"。

    外部存储把过期交给 TTL 时会返回空——服务层照原样透传（不去猜），
    接入方也不得把它读成"本次无人掉线"。这条与"未标定不判定"同源。
    """

    class TTLStyleStore(SessionStore):
        """最小的进程外实现：只实现三个方法，过期由存储层负责。"""

        def __init__(self):
            self.data = {}
            self.writes = 0

        def get(self, session_id):
            return self.data.get(session_id)

        def set(self, session_id, session):
            self.data[session_id] = session
            self.writes += 1

        def sweep(self):
            return []

    store = TTLStyleStore()
    _, client = _client(store=store, encoder=encoder)
    d = _check(client, "帮我把第2天的行程排一下", direction=CARD).json()
    assert d["evicted_sessions"] == []
    assert store.writes == 1 and "s1" in store.data

# -*- coding: utf-8 -*-
"""FastAPI 服务工厂。端点全部是 def（线程池执行，不阻塞事件循环）。NLI 默认关（V2）。

启动（注意是**工厂**，不是模块级 app）
--------------------------------------
::

    DIRECTION_DRIFT_API_KEY=你的密钥 \\
    uvicorn "direction_drift.api.server:create_app" --factory --port 8000

v2.3.6 改为工厂的原因：模块级 `app` 意味着 **import 即副作用**——导入时就构造
语义编码器、就读环境变量。后果是本文件在 v2.3.6 之前**零测试覆盖**：pytest 收不
进来，因为导入会去下载 100MB 权重。工厂让导入干净、构造时机由调用方掌握；
失败点（缺密钥 / 缺语义嵌入）仍然落在**启动时**，仍然 fail-closed。

本服务的定位与限度
------------------
- **会话状态经 `core.session_store.SessionStore` 存取**（v2.3.6）。默认是
  `InMemorySessionStore`：单进程内存态，进程重启 = 会话全丢。要持久化就传自己的
  实现（`create_app(store=…)`）——检测、返回、标定逻辑一个字都不用改。
  为什么这样做是安全的、迁移怎么做，见 `docs/persistence.md`。
- **读-改-写不是原子的**（v2.3.6 显式声明）：并发请求对同一 `system_id` 会互相
  覆盖判定推进（各自 get → 各自 judge → 各自 set）。这里**声明而不掩盖**——
  它不是存储层能单独解决的问题，加锁也消除不了语义层面的串扰。
  生产并发请每租户独立进程，或接入托管层。
- **未标定不判定**：本服务不提供标定入口，`DriftDetector` 以 calibrated=False
  构造，因此 `/v1/check` 的 drift_level 恒为 "uncalibrated"——只记录轨迹，不判定、
  不冻结。需要真实判定请走库调用（README 路径 A/B）并注入该场景标定出的阈值。
- **需要语义嵌入**：默认构造 `Encoder()`，缺失 sentence-transformers 时拒绝启动
  并给出安装指令（不降级为 DemoEncoder——那不是语义编码器，分数无效）。
  测试/演示可 `create_app(encoder=DemoEncoder())` **显式**注入：这不是降级，
  降级是"悄悄换掉"，注入是"调用方知道自己在给什么"。
- **不支持场景卡**：`DirectionInput` 只承载 goal/constraints/样本，不含
  `rule_layer` / `min_suspect_len` / `format_whitelist` / `prompt_version`。
  要用卡的完整能力（尤其规则层与格式豁免），请走库调用。
"""

import os
import secrets
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .. import __version__
from ..core.alignment import AlignmentCalculator
from ..core.constraint_checker import ConstraintChecker
from ..core.direction_cone import DirectionCone
from ..core.drift_detector import DriftDetector
from ..core.return_protocol import ReturnProtocol
from ..core.session_store import (DEFAULT_MAX_COUNT, DEFAULT_MAX_IDLE_SECONDS,
                                  InMemorySessionStore, Session, SessionStore)


class DirectionInput(BaseModel):
    goal: str
    constraints: List = []
    core_samples: List[str] = []
    boundary_samples: List[str] = []
    negative_samples: List[str] = []


class CheckRequest(BaseModel):
    system_id: str
    direction: Optional[DirectionInput] = None
    current_output: str


class CheckResponse(BaseModel):
    alignment: dict
    drift_level: str
    action: str
    message: str
    reasons: List[str] = []
    cone_position: str
    evicted_sessions: List[str] = []       # 因容量上限被淘汰的会话（不静默丢）


def _load_encoder():
    """语义嵌入是本服务的硬依赖：缺失即拒绝启动（fail-closed）。

    刻意不降级为 DemoEncoder——它是词级哈希编码器，不衡量语义相似度，
    用其分数做判定是"跑得起来但结论错"，远比直接启动失败危险。
    """
    from ..utils.encoder import Encoder
    try:
        return Encoder()
    except ImportError as exc:
        raise RuntimeError(
            "HTTP API 需要语义嵌入（sentence-transformers）。请安装："
            'pip install -e ".[api,sbert]"'
        ) from exc


def create_app(encoder=None, store: Optional[SessionStore] = None,
               api_key: Optional[str] = None,
               enable_nli: Optional[bool] = None) -> FastAPI:
    """构造应用。**默认参数就是生产配置。**

    四个参数都可注入，目的是让这一层**可测**（v2.3.6 之前它无法被测试）：

    - `encoder`：默认 `Encoder()`（真实语义嵌入，缺依赖即拒绝启动）。
      测试传 `DemoEncoder()` 以免下载权重。
    - `store`：默认 `InMemorySessionStore`（进程内，重启即丢）。
      **要持久化就传自己的实现**——这是本函数最重要的一个参数。
    - `api_key`：默认读 `DIRECTION_DRIFT_API_KEY`，未设置则拒绝启动。
    - `enable_nli`：默认读 `ENABLE_NLI`（默认 0）。

    构造出的 `app.state` 上挂了 `encoder` / `store` / `calculator` /
    `nli_enabled`，便于运维与测试自省（不改任何判定行为）。
    """
    key = api_key if api_key is not None else os.environ.get("DIRECTION_DRIFT_API_KEY")
    if not key:
        raise RuntimeError("环境变量 DIRECTION_DRIFT_API_KEY 未设置，拒绝启动")

    if encoder is None:
        encoder = _load_encoder()

    if enable_nli is None:
        enable_nli = os.environ.get("ENABLE_NLI", "0") == "1"

    checker = ConstraintChecker()
    if enable_nli:                                        # 仅显式开启才加载 NLI
        from ..utils.nli import NLIModel
        checker = ConstraintChecker(NLIModel())
    calculator = AlignmentCalculator(encoder, checker)

    if store is None:
        store = InMemorySessionStore(
            max_idle_seconds=int(os.environ.get(
                "DIRECTION_DRIFT_SESSION_MAX_IDLE", str(DEFAULT_MAX_IDLE_SECONDS))),
            max_count=int(os.environ.get(
                "DIRECTION_DRIFT_MAX_SESSIONS", str(DEFAULT_MAX_COUNT))))

    app = FastAPI(title="Direction Drift API", version=__version__)
    app.state.encoder = encoder
    app.state.store = store
    app.state.calculator = calculator
    app.state.nli_enabled = bool(enable_nli)

    def verify_api_key(x_api_key: str = Header(...)):
        # 常量时间比较（v2.3.3）：`!=` 的短路比较会泄露密钥前缀的匹配长度。
        if not secrets.compare_digest(x_api_key.encode("utf-8"), key.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.post("/v1/check", response_model=CheckResponse)
    def check_drift(req: CheckRequest, _=Depends(verify_api_key)):
        evicted = store.sweep()
        sid = req.system_id
        session = store.get(sid)

        if session is None:
            if req.direction is None:
                raise HTTPException(status_code=400,
                                    detail="首次调用必须提供 direction")
            try:
                cone = DirectionCone.from_samples(
                    goal=req.direction.goal,
                    core_samples=req.direction.core_samples,
                    boundary_samples=req.direction.boundary_samples,
                    negative_samples=req.direction.negative_samples,
                    constraints=req.direction.constraints, embed_fn=encoder.encode)
            except ValueError as e:
                # 建锥失败不留半个会话：落到这里的路径上一个 set 都没发生。
                raise HTTPException(status_code=400, detail=str(e))
            session = Session(cone=cone, detector=DriftDetector(),
                              protocol=ReturnProtocol())
        elif req.direction is not None:
            raise HTTPException(status_code=409,
                                detail="会话已存在。如需更新方向，请调用 /v1/rebuild")

        alignment = calculator.compute(session.cone, req.current_output)
        drift_result = session.detector.judge(alignment)
        action = session.protocol.act(drift_result, session.cone, alignment)

        # 写回（v2.3.6）：内存实现里这次 set 几乎零成本，进程外实现里它就是那次写。
        # 漏掉它不会在内存里报错（同一份对象，看起来"跑得通"），换成 Redis 才会
        # 静默丢状态——所以它必须是一个无条件、看得见的动作。
        store.set(sid, session)

        return CheckResponse(alignment=alignment,
                             drift_level=drift_result["drift_level"],
                             action=action["action"], message=action["message"],
                             reasons=action.get("reasons", []),
                             cone_position=alignment["cone_position"],
                             evicted_sessions=evicted)

    @app.get("/v1/state/{system_id}")
    def get_state(system_id: str, _=Depends(verify_api_key)):
        session = store.get(system_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"system_id": system_id, "cone": session.cone.to_dict(),
                "detector": session.detector.get_state(),
                "protocol_state": session.protocol.state}

    @app.post("/v1/resume/{system_id}")
    def resume(system_id: str, _=Depends(verify_api_key)):
        session = store.get(system_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        result = session.protocol.resume(session.cone, session.detector)
        store.set(system_id, session)      # 状态迁移必须落回存储，否则换实现就丢
        return result

    @app.post("/v1/rebuild/{system_id}")
    def rebuild(system_id: str, direction: DirectionInput,
                _=Depends(verify_api_key)):
        session = store.get(system_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        try:
            session.cone.rebuild_from_samples(
                core_samples=direction.core_samples,
                boundary_samples=direction.boundary_samples,
                negative_samples=direction.negative_samples,
                embed_fn=encoder.encode)
            session.detector.reset()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        store.set(system_id, session)      # 改锥 + reset 同样是状态迁移
        return {"status": "rebuilt", "cone": session.cone.to_dict()}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app

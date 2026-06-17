"""
P1-4 FastAPI app 工厂
=====================
- create_app(): 构 FastAPI app + CORS(只放 GET)+ 挂 5 router.
- lifespan: startup 加载 WCPredictor 单例(避免每请求 from_artifacts 读盘);
  DC artifacts 缺失 → app.state.predictor=None(比赛预测端点返 503), 不崩.

运行: uvicorn backend.api.app:create_app --factory --port 8000
      python -m backend.api
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routers import health, matches, methodology, teams, tournament

log = logging.getLogger("wc.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """startup: 加载 WCPredictor; shutdown: 无资源需释放."""
    try:
        from backend.models.match_predictor import WCPredictor
        app.state.predictor = WCPredictor()
        app.state.dc_loaded = True
        log.info("WCPredictor 加载完成(单例)")
    except Exception as e:
        app.state.predictor = None
        app.state.dc_loaded = False
        log.warning("DC artifacts 未加载(比赛预测端点将 503): %s", e)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="World Cup 2026 Probability Dashboard API",
        version="0.1.0",
        description="只读端点: 锦标赛晋级/夺冠概率 + 单场赛前预测 + 方法论. worker 后台写 DB.",
        lifespan=lifespan,
    )
    origins = [o.strip() for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET"],        # 只读端点, 收紧
        allow_headers=["*"],
    )
    for r in (health.router, teams.router, tournament.router, matches.router, methodology.router):
        app.include_router(r)
    return app

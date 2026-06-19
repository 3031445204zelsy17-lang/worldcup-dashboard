"""
P1-4 FastAPI app 工厂
=====================
- create_app(): 构 FastAPI app + CORS(只放 GET)+ 挂 5 router.
- 同源 SPA(P1-7): 生产单 Azure 域名, uvicorn 同时 serve API + 前端 dist;
  dev 无 dist → 纯 API(走 vite dev server 5173 + proxy /api).
- lifespan: startup 加载 WCPredictor 单例(避免每请求 from_artifacts 读盘);
  DC artifacts 缺失 → app.state.predictor=None(比赛预测端点返 503), 不崩.

运行: uvicorn backend.api.app:create_app --factory --port 8000
      python -m backend.api
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routers import health, matches, methodology, teams, tournament

log = logging.getLogger("wc.api")

# 前端构建产物(生产同源: 单域名 serve API + SPA; dev 无 dist → 纯 API 不报错).
# app.py 在 backend/api/, parents[2] = 项目根(本地)/ /app(容器)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


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

    # —— 同源 SPA(P1-7 单 Azure 域名方案): API 在 /api/*, 其余路径 → 前端 ——
    if FRONTEND_DIST.is_dir():
        assets_dir = FRONTEND_DIST / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        # catch-all 必须在所有 API router 之后注册: /api 未匹配 → 真 404;
        # 根级真实静态文件(favicon/vite.svg 等)直返; 其余客户端路由 → index.html(React Router 接管)
        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_fallback(full_path: str):
            if full_path.startswith("api"):
                raise HTTPException(status_code=404, detail="Not found")
            candidate = FRONTEND_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

        log.info("前端已挂载(%s): 同源 SPA 模式", FRONTEND_DIST)
    else:
        log.info("无 frontend/dist → 纯 API 模式(dev)")

    return app

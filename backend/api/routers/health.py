"""GET /api/health —— 健康检查(DB 可读?计数 + 最近重算时间 + DC 加载状态).

DB 抽风不返 500(健康探活语义): db_readable=False + status=degraded, 仍 HTTP 200,
让监控看 body 而非把偶发 DB 锁误当服务宕机.
"""
from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Request

from backend.api import deps, queries
from backend.api.schemas import HealthResponse

router = APIRouter(tags=["health"])
log = logging.getLogger("wc.api.health")


@router.get("/api/health", response_model=HealthResponse)
def health(request: Request) -> dict:
    dc_loaded = bool(getattr(request.app.state, "dc_loaded", False))
    path = deps.db_path()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout = 2000")
        counts = queries.health_counts(conn)
        last = queries.last_recomputed(conn)
        conn.close()
        return {"status": "ok", "db_readable": True, "counts": counts,
                "last_mc_recomputed_at": last, "dc_artifacts_loaded": dc_loaded}
    except Exception as e:                       # DB 不存在 / 锁 / 损坏 → degraded(仍 200)
        log.warning("health 检查 DB 失败: %s", e)
        return {"status": "degraded", "db_readable": False, "counts": {},
                "last_mc_recomputed_at": None, "dc_artifacts_loaded": dc_loaded}

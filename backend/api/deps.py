"""
P1-4 API 依赖注入
================
- get_db: 每请求只读 SQLite 连接(``mode=ro`` + busy_timeout + query_only).
  WAL 下读不阻塞 worker 写; mode=ro 物理禁止写(防 bug 误写).
- get_predictor: app.state 的 WCPredictor 单例(startup 加载一次, 避免每请求读盘).
  未加载 → 503.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Generator

from fastapi import HTTPException, Request

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API_DB = ROOT / "data" / "wc.db"


def db_path() -> Path:
    """API_DB env(默认 data/wc.db). 相对路径以 ROOT 为基."""
    p = os.environ.get("API_DB", str(DEFAULT_API_DB))
    pp = Path(p)
    return pp if pp.is_absolute() else (ROOT / p)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI 依赖: 每请求开只读连接, 请求结束关闭."""
    path = db_path()
    # mode=ro: 只读(DB 不存在时抛, 调用方应确保 worker 已建库); uri=True 启用查询串.
    # check_same_thread=False: FastAPI 同步端点跑在 anyio 线程池, yield 依赖的
    #   setup/endpoint/teardown 可能被调度到不同 worker thread, 默认 True 会抛
    #   ProgrammingError. 每请求独立连接 + 请求内串行使用 → 跨线程安全.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 2000")   # worker 正在写时最多等 2s 而非立即 locked
    conn.execute("PRAGMA query_only = ON")        # 双保险: 物理禁止任何写
    try:
        yield conn
    finally:
        conn.close()


def get_predictor(request: Request):
    """app.state.predictor(WCPredictor 单例). 未就绪 → 503(比赛预测端点依赖)."""
    pred = getattr(request.app.state, "predictor", None)
    if pred is None:
        raise HTTPException(status_code=503,
                            detail="预测引擎未就绪(DC artifacts 未加载或加载失败)")
    return pred

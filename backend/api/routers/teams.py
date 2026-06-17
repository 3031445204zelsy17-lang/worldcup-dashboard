"""GET /api/teams —— 48 队列表(group 过滤, sort=elo|name; rank 总按 elo 排名)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from backend.api import queries
from backend.api.deps import get_db
from backend.api.schemas import TeamsResponse

router = APIRouter(tags=["teams"])


@router.get("/api/teams", response_model=TeamsResponse)
def list_teams(group: str | None = None, sort: str = "elo",
               conn: sqlite3.Connection = Depends(get_db)) -> dict:
    rows = queries.list_teams(conn, group=group, sort="elo")   # 先按 elo 算 rank
    ranked = [{"name": r["name"], "group": r["group"], "elo": r["elo"], "rank": i + 1}
              for i, r in enumerate(rows)]
    if sort == "name":                          # sort 只改显示顺序, rank 仍是 elo 实力排名
        ranked.sort(key=lambda x: x["name"])
    return {"teams": ranked, "count": len(ranked)}

"""GET /api/tournament, /api/tournament/{team} —— 夺冠榜(视图切换) + 球队晋级阶梯."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from backend.api import queries
from backend.api.deps import get_db
from backend.api.schemas import TournamentResponse, TeamDetailResponse

router = APIRouter(tags=["tournament"])

VIEWS = {"win", "group", "ro32", "ro16", "qf", "sf", "final"}


@router.get("/api/tournament", response_model=TournamentResponse)
def tournament(view: str = "win",
               conn: sqlite3.Connection = Depends(get_db)) -> dict:
    if view not in VIEWS:
        raise HTTPException(400, f"view 非法, 可选: {sorted(VIEWS)}")
    teams = queries.tournament_teams(conn)
    last = queries.last_recomputed(conn)
    out = []
    for t in teams:
        adv = t["advancement"]
        out.append({
            "name": t["name"], "group": t["group"], "elo": t["elo"],
            "win_prob": t["win_prob"],
            "advancement": {r: adv.get(r, 0.0) for r in queries.ROUNDS},
            "sort_value": queries.sort_value(t, view),
        })
    out.sort(key=lambda x: x["sort_value"], reverse=True)
    return {"view": view, "last_recomputed_at": last, "teams": out}


@router.get("/api/tournament/{team}", response_model=TeamDetailResponse)
def team_detail(team: str,
                conn: sqlite3.Connection = Depends(get_db)) -> dict:
    tr = queries.team_row(conn, team)
    if tr is None:
        raise HTTPException(404, f"未知队: {team}")
    probs = queries.team_probs(conn, team)
    if probs is None:
        raise HTTPException(404, f"无锦标赛概率数据: {team}")
    matches = queries.filter_matches(queries.all_matches(conn), team=team)
    return {
        "name": tr["name"], "group": tr["group"], "elo": tr["elo"],
        "rank": queries.team_rank(conn, team) or 0,
        "advancement_path": queries.advancement_path(probs),
        "matches": [queries.match_to_summary(m) for m in matches],
        "drivers": queries.team_drivers(conn, team),
    }

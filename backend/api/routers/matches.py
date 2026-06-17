"""GET /api/matches, /api/matches/{match_key} —— 比赛列表 + 详情(实时赛前预测).

match_key 格式 ``date|home|away``(含 ``|``); 前端须 ``encodeURIComponent``.
详情端点实时调 WCPredictor.predict(不存 predictions 表, 该表留 P2 实时曲线).
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from backend.data.cache import MatchCache
from backend.api import queries
from backend.api.deps import get_db, get_predictor
from backend.api.schemas import MatchesResponse, MatchDetailResponse

router = APIRouter(tags=["matches"])


@router.get("/api/matches", response_model=MatchesResponse)
def list_matches(date: str | None = None, team: str | None = None,
                 status: str | None = None, limit: int | None = None,
                 conn: sqlite3.Connection = Depends(get_db)) -> dict:
    matches = queries.all_matches(conn)
    filtered = queries.filter_matches(matches, date=date, team=team,
                                      status=status, limit=limit)
    return {
        "filters": {"date": date, "team": team, "status": status, "limit": limit},
        "count": len(filtered),
        "matches": [queries.match_to_summary(m) for m in filtered],
    }


@router.get("/api/matches/{match_key}", response_model=MatchDetailResponse)
def match_detail(match_key: str, predict: bool = True,
                 conn: sqlite3.Connection = Depends(get_db),
                 predictor=Depends(get_predictor)) -> dict:
    m = MatchCache(conn).get(match_key)
    if m is None:
        raise HTTPException(404, f"未知比赛: {match_key}")
    prediction = None
    if predict:
        prediction = queries.serialize_prediction(predictor.predict(m.home, m.away))
    score = None
    if m.home_score is not None and m.away_score is not None:
        score = {"home": m.home_score, "away": m.away_score}
    return {
        "match": queries.match_to_summary(m),
        "prediction": prediction,
        "score": score,
        "drivers": queries.match_drivers(conn, m),
    }

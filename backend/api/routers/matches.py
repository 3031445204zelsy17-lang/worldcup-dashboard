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
from backend.api.schemas import MatchesResponse, MatchDetailResponse, LiveResponse

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


@router.get("/api/matches/{match_key}/live", response_model=LiveResponse)
def match_live(match_key: str,
               conn: sqlite3.Connection = Depends(get_db),
               predictor=Depends(get_predictor)) -> dict:
    """P2-1 赛中实时胜率: predictions 时间线(worker live_tick 写) + 当前比分/分钟.

    data_source: live_poisson(有 worker 写的实时曲线) / pre_match(降级赛前预测) / unavailable.
    符合「用户访问 0 次外部 API」—— 本端点只读 DB, 不调 ESPN(worker 负责).
    """
    m = MatchCache(conn).get(match_key)
    if m is None:
        raise HTTPException(404, f"未知比赛: {match_key}")
    mid_row = conn.execute("SELECT id FROM matches WHERE match_key=?", (match_key,)).fetchone()
    timeline = queries.prediction_timeline(conn, mid_row[0]) if mid_row else []
    if timeline:
        latest = timeline[-1]
        minute = latest["minute"]
        live_prob = {"home_win": latest["home_win"], "draw": latest["draw"],
                     "away_win": latest["away_win"]}
        source = "live_poisson"
    elif predictor is not None:
        pred = predictor.predict(m.home, m.away)
        minute = 0
        live_prob = {"home_win": float(pred["home_win"]), "draw": float(pred["draw"]),
                     "away_win": float(pred["away_win"])}
        source = "pre_match"
    else:
        minute = 0
        live_prob = {"home_win": None, "draw": None, "away_win": None}
        source = "unavailable"
    current_score = None
    if m.home_score is not None and m.away_score is not None:
        current_score = {"home": m.home_score, "away": m.away_score}
    return {
        "match": queries.match_to_summary(m),
        "minute": minute,
        "current_score": current_score,
        "live_win_prob": live_prob,
        "win_prob_timeline": timeline,
        "is_live": m.status.value == "live",
        "data_source": source,
    }

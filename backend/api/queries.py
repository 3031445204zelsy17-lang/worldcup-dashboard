"""
P1-4 API 纯函数查询层
====================
SQL 集中于此, router 只编排. 每函数接 ``sqlite3.Connection``, 返回 dict/list(纯数据,
不含 Pydantic model —— 由 router 套 model). 含 numpy/parquet → JSON 的序列化辅助.

复用: matches 读取走 backend.data.cache.MatchCache(已 JOIN teams 回填 name).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKTEST_PARQUET = ROOT / "data" / "processed" / "backtest_2018_2022.parquet"
CALIBRATION_PARQUET = ROOT / "data" / "processed" / "backtest_calibration.parquet"

ROUNDS = ("group", "ro32", "ro16", "qf", "sf", "final")
ROUND_LABELS = {  # 晋级阶梯中文标签(前端展示)
    "group": "小组出线", "ro32": "32强", "ro16": "16强",
    "qf": "8强", "sf": "半决赛", "final": "决赛", "win": "夺冠",
}


# ============================================================
# 健康检查
# ============================================================
def health_counts(conn: sqlite3.Connection) -> dict:
    counts = {}
    for tbl in ("teams", "matches", "tournament_probs"):
        counts[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    counts["matches_finished"] = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
    return counts


def last_recomputed(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(calculated_at) FROM tournament_probs").fetchone()
    return row[0] if row and row[0] is not None else None


# ============================================================
# 球队
# ============================================================
def list_teams(conn: sqlite3.Connection, group: str | None = None,
               sort: str = "elo") -> list[dict]:
    order = "elo_rating DESC" if sort == "elo" else "name ASC"
    sql = "SELECT name, group_name, elo_rating FROM teams"
    params: tuple = ()
    if group:
        sql += " WHERE group_name=?"
        params = (group,)
    sql += f" ORDER BY {order}"
    rows = conn.execute(sql, params).fetchall()
    return [{"name": r[0], "group": r[1], "elo": r[2]} for r in rows]


def team_row(conn: sqlite3.Connection, name: str) -> dict | None:
    r = conn.execute(
        "SELECT name, group_name, elo_rating FROM teams WHERE name=?", (name,)).fetchone()
    if not r:
        return None
    return {"name": r[0], "group": r[1], "elo": r[2]}


def team_rank(conn: sqlite3.Connection, name: str) -> int | None:
    """elo 降序排名(48 队小表). elo 为 NULL → None."""
    elo = conn.execute("SELECT elo_rating FROM teams WHERE name=?", (name,)).fetchone()
    if not elo or elo[0] is None:
        return None
    rnk = conn.execute(
        "SELECT COUNT(*)+1 FROM teams WHERE elo_rating > ?", (elo[0],)).fetchone()
    return int(rnk[0])


# ============================================================
# 锦标赛概率
# ============================================================
def tournament_teams(conn: sqlite3.Connection) -> list[dict]:
    """全部 48 队聚合: 每队 win_prob + 6 轮 advancement(按 elo 降序)."""
    rows = conn.execute(
        """SELECT t.name, t.group_name, t.elo_rating,
                  tp.round, tp.advancement_prob, tp.win_prob
           FROM tournament_probs tp JOIN teams t ON t.id = tp.team_id
           ORDER BY t.elo_rating DESC""").fetchall()
    by_team: dict[str, dict] = {}
    for name, grp, elo, rnd, adv, win in rows:
        t = by_team.setdefault(name, {"name": name, "group": grp, "elo": elo,
                                      "win_prob": float(win), "advancement": {}})
        t["advancement"][rnd] = float(adv)
    return list(by_team.values())


def team_probs(conn: sqlite3.Connection, name: str) -> dict | None:
    """单队 6 轮 advancement + win_prob. 无数据 → None."""
    rows = conn.execute(
        """SELECT tp.round, tp.advancement_prob, tp.win_prob
           FROM tournament_probs tp JOIN teams t ON t.id = tp.team_id
           WHERE t.name=?""", (name,)).fetchall()
    if not rows:
        return None
    return {"advancement": {r[0]: float(r[1]) for r in rows}, "win_prob": float(rows[0][2])}


def advancement_path(probs: dict) -> list[dict]:
    """probs → 晋级阶梯 7 格(6 轮 + 夺冠)."""
    adv = probs["advancement"]
    path = [{"round": r, "prob": adv.get(r, 0.0), "label": ROUND_LABELS[r]} for r in ROUNDS]
    path.append({"round": "win", "prob": probs["win_prob"], "label": ROUND_LABELS["win"]})
    return path


def sort_value(team: dict, view: str) -> float:
    """view → 排序值(win 取 win_prob, 否则取对应轮 advancement)."""
    if view == "win":
        return team["win_prob"]
    return team["advancement"].get(view, 0.0)


# ============================================================
# 比赛
# ============================================================
def all_matches(conn: sqlite3.Connection) -> list:
    """全部比赛(MatchCache.get_all: 已 JOIN teams 回填 name, ORDER BY kickoff)."""
    from backend.data.cache import MatchCache
    return MatchCache(conn).get_all()


def match_to_summary(m) -> dict:
    return {
        "match_key": m.match_key, "date": m.date, "kickoff": m.kickoff,
        "home": m.home, "away": m.away,
        "home_score": m.home_score, "away_score": m.away_score,
        "status": m.status.value, "neutral": bool(m.neutral), "stage": m.stage,
    }


def filter_matches(matches, *, date: str | None = None, team: str | None = None,
                   status: str | None = None, limit: int | None = None) -> list:
    """Python 层过滤(date/team/status). date='today' → server 本地日."""
    import datetime as _dt
    if date == "today":
        date = _dt.date.today().isoformat()
    out = []
    for m in matches:
        if date and m.date != date:
            continue
        if team and team not in (m.home, m.away):
            continue
        if status and m.status.value != status:
            continue
        out.append(m)
    if limit is not None:
        out = out[:limit]
    return out


# ============================================================
# 序列化: predict dict → JSON 友好
# ============================================================
def serialize_prediction(pred: dict) -> dict:
    """WCPredictor.predict() 返回的 dict → JSON 友好(ndarray→list, tuple→list, np→py)."""
    sm = pred["score_matrix"]
    matrix = sm.tolist() if hasattr(sm, "tolist") else sm
    return {
        "home": pred["home"], "away": pred["away"],
        "neutral": bool(pred["neutral"]),
        "host_home": bool(pred["host_home"]), "host_away": bool(pred["host_away"]),
        "home_win": float(pred["home_win"]), "draw": float(pred["draw"]),
        "away_win": float(pred["away_win"]),
        "lambda_home": float(pred["lambda_home"]), "lambda_away": float(pred["lambda_away"]),
        "expected_home": float(pred["expected_home"]), "expected_away": float(pred["expected_away"]),
        "top_scores": [[int(h), int(a), float(p)] for h, a, p in pred["top_scores"]],
        "score_matrix": matrix,
        "max_goals": len(matrix) - 1 if matrix else 0,
    }


# ============================================================
# 驱动因素(P1 多为空 → null + data_status)
# ============================================================
def match_drivers(conn: sqlite3.Connection, m) -> dict:
    """比赛驱动因素: elo 差 + 东道主. altitude/weather/injuries 表空 → null."""
    from backend.models.match_predictor import WC2026_HOSTS
    he = conn.execute("SELECT elo_rating FROM teams WHERE name=?", (m.home,)).fetchone()
    ae = conn.execute("SELECT elo_rating FROM teams WHERE name=?", (m.away,)).fetchone()
    he = he[0] if he and he[0] is not None else None
    ae = ae[0] if ae and ae[0] is not None else None
    host_adv = (m.home in WC2026_HOSTS) ^ (m.away in WC2026_HOSTS)   # 恰一方东道主
    return {
        "home_elo": he, "away_elo": ae,
        "elo_gap": (he - ae) if (he is not None and ae is not None) else None,
        "neutral": bool(m.neutral), "host_advantage": bool(host_adv),
        "altitude": None, "weather": None,
        "injuries": {"home": [], "away": []},
        "data_status": "pending",
    }


def team_drivers(conn: sqlite3.Connection, name: str) -> dict:
    """球队驱动因素: elo 与均值差. 近期战绩/injuries 空 → null."""
    row = conn.execute(
        "SELECT elo_rating, (SELECT AVG(elo_rating) FROM teams) FROM teams WHERE name=?",
        (name,)).fetchone()
    if not row or row[0] is None:
        return {"recent_form": None, "injuries": [], "data_status": "pending"}
    elo, avg = row[0], row[1]
    return {
        "elo_gap_vs_avg": float(elo - avg) if avg is not None else None,
        "recent_form": None, "injuries": None,
        "data_status": "pending",
    }


# ============================================================
# 方法论(读 backtest parquet)
# ============================================================
def backtest_summary(parquet: Path = BACKTEST_PARQUET) -> dict | None:
    """三变体 accuracy/brier(128 场). parquet 不存在 → None."""
    if not Path(parquet).exists():
        return None
    import pandas as pd
    df = pd.read_parquet(parquet)
    variants = {}
    for v in ("elo", "dc", "dcs"):
        variants[v] = {
            "accuracy": float(df[f"{v}_hit"].mean()),
            "brier": float(df[f"{v}_brier"].mean()),
        }
    return {
        "n_matches": int(len(df)),
        "tournaments": sorted({int(y) for y in df["year"].unique()}),
        "variants": variants,
        "production_variant": "dcs",
        "target_range": "53-55%",
    }


def calibration_rows(parquet: Path = CALIBRATION_PARQUET) -> list[dict]:
    """reliability 数据(variant×outcome×decile). parquet 不存在 → []. 用 to_json 转 numpy→py."""
    if not Path(parquet).exists():
        return []
    import pandas as pd
    df = pd.read_parquet(parquet)
    return json.loads(df.to_json(orient="records"))

"""
P1-4 API 纯函数查询层
====================
SQL 集中于此, router 只编排. 每函数接 ``sqlite3.Connection``, 返回 dict/list(纯数据,
不含 Pydantic model —— 由 router 套 model). 含 numpy/parquet → JSON 的序列化辅助.

复用: matches 读取走 backend.data.cache.MatchCache(已 JOIN teams 回填 name).
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKTEST_PARQUET = ROOT / "data" / "processed" / "backtest_2018_2022.parquet"
CALIBRATION_PARQUET = ROOT / "data" / "processed" / "backtest_calibration.parquet"
DC_PARQUET = ROOT / "data" / "processed" / "dixon_coles_current.parquet"
DC_JSON = ROOT / "data" / "processed" / "dixon_coles_global.json"

ROUNDS = ("group", "ro32", "ro16", "qf", "sf", "final")
ROUND_LABELS = {  # 晋级阶梯中文标签(前端展示)
    "group": "小组出线", "ro32": "32强", "ro16": "16强",
    "qf": "8强", "sf": "半决赛", "final": "决赛", "win": "夺冠",
}

# Monte Carlo 抽样规模(与 simulation/mc.DEFAULT_N 对齐) —— 置信带宽度据此算
MC_N = 10000
Z95 = 1.959963984540054   # 95% 双侧正态分位数


# ============================================================
# 统计: Wilson 置信区间 + DC artifacts 缓存(P1-6 透明度层)
# ============================================================
def wilson_interval(p: float, n: int = MC_N) -> tuple[float, float]:
    """Wilson 95% 置信区间 —— 量化 Monte Carlo 抽样不确定性.

    给定某队某轮的晋级/夺冠概率 p̂ = reach/N, 区间反映"重跑 MC 该概率的波动范围".
    边界稳健(优于正态近似 p±z√): p 接近 0/1 时不溢出、不对称. clamp 到 [0,1].
    """
    if n <= 0:
        return (0.0, 1.0)
    p = min(max(float(p), 0.0), 1.0)
    z2 = Z95 * Z95
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


_DC_CACHE = None          # DixonColes 单例(进程级)
_DC_TRIED = False         # 是否已尝试加载(避免每请求重复 try)


def load_dc():
    """lazy 加载 DC artifacts(进程内单例). artifacts 缺失 → None(drivers 降级 null).

    drivers 查询用攻防参数做"模型依据展开"; 与 lifespan 的 WCPredictor 单例独立
    (此处只需 attack/defense/mu/gamma, 不需分组/东道主逻辑, 避免引入 match_predictor 依赖).
    """
    global _DC_CACHE, _DC_TRIED
    if _DC_TRIED:
        return _DC_CACHE
    _DC_TRIED = True
    try:
        from backend.models.dixon_coles import DixonColes
        _DC_CACHE = DixonColes.from_artifacts(DC_PARQUET, DC_JSON)
    except Exception:
        _DC_CACHE = None
    return _DC_CACHE


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
    """probs → 晋级阶梯 7 格(6 轮 + 夺冠), 每格带 95% Wilson 置信区间(MC 抽样不确定性)."""
    adv = probs["advancement"]
    path = []
    for r in ROUNDS:
        p = adv.get(r, 0.0)
        lo, hi = wilson_interval(p)
        path.append({"round": r, "prob": p, "ci_low": lo, "ci_high": hi, "label": ROUND_LABELS[r]})
    wp = probs["win_prob"]
    wlo, whi = wilson_interval(wp)
    path.append({"round": "win", "prob": wp, "ci_low": wlo, "ci_high": whi, "label": ROUND_LABELS["win"]})
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
# 驱动因素(P1-6: elo + DC 攻防参数; altitude/weather/injuries 留 P2 → null)
# ============================================================
def _dc_ranks(conn: sqlite3.Connection) -> dict:
    """48 参赛队的 DC 攻防参数排名. attack 降序(1=最强进攻), defense 降序(1=最少失球).

    DC 参数化 ln λ=μ+att−def(参数 exp 化存储, 均值≈1): attack 在分子(λ=exp(μ)·攻·1/防·exp(γ))
    → attack 大=自己进球多=强攻; defense 在分母 → defense 大=对方 λ 小=少失球=强守.
    故两者同向: 大=强, rank 1=该维度最强. 每次重算(48 队排序微秒级, 避免跨测试 DB 缓存污染).
    """
    dc = load_dc()
    if dc is None:
        return {}
    teams48 = [r[0] for r in conn.execute("SELECT name FROM teams").fetchall()]
    present = [t for t in teams48 if t in getattr(dc, "attack", {})]
    by_att = sorted(present, key=lambda t: dc.attack[t], reverse=True)
    by_def = sorted(present, key=lambda t: dc.defense[t], reverse=True)
    return {t: {"attack_rank": by_att.index(t) + 1, "defense_rank": by_def.index(t) + 1}
            for t in present}


def match_drivers(conn: sqlite3.Connection, m) -> dict:
    """比赛驱动因素: elo 差 + 东道主 + DC 攻防参数(λ 公式可解释). P2 数据空 → null."""
    from backend.models.match_predictor import WC2026_HOSTS
    he = conn.execute("SELECT elo_rating FROM teams WHERE name=?", (m.home,)).fetchone()
    ae = conn.execute("SELECT elo_rating FROM teams WHERE name=?", (m.away,)).fetchone()
    he = he[0] if he and he[0] is not None else None
    ae = ae[0] if ae and ae[0] is not None else None
    host_adv = (m.home in WC2026_HOSTS) ^ (m.away in WC2026_HOSTS)   # 恰一方东道主
    dc = load_dc()
    ranks = _dc_ranks(conn)
    hr = ranks.get(m.home, {})
    ar = ranks.get(m.away, {})
    return {
        "home_elo": he, "away_elo": ae,
        "elo_gap": (he - ae) if (he is not None and ae is not None) else None,
        "neutral": bool(m.neutral), "host_advantage": bool(host_adv),
        # DC 攻防参数(λ=exp(μ)·攻主/防客·exp(γ·¬neutral), 客队同理无 γ)
        "home_attack": float(dc.attack[m.home]) if dc and m.home in dc.attack else None,
        "home_defense": float(dc.defense[m.home]) if dc and m.home in dc.defense else None,
        "away_attack": float(dc.attack[m.away]) if dc and m.away in dc.attack else None,
        "away_defense": float(dc.defense[m.away]) if dc and m.away in dc.defense else None,
        "home_attack_rank": hr.get("attack_rank"), "home_defense_rank": hr.get("defense_rank"),
        "away_attack_rank": ar.get("attack_rank"), "away_defense_rank": ar.get("defense_rank"),
        "global_mu": float(dc.mu) if dc else None, "global_gamma": float(dc.gamma) if dc else None,
        "altitude": None, "weather": None,
        "injuries": {"home": [], "away": []},
        "data_status": "pending",
    }


def team_drivers(conn: sqlite3.Connection, name: str) -> dict:
    """球队驱动因素: elo 与均值差 + DC 攻防参数及 48 队排名. 近期战绩/injuries 空 → null."""
    row = conn.execute(
        "SELECT elo_rating, (SELECT AVG(elo_rating) FROM teams) FROM teams WHERE name=?",
        (name,)).fetchone()
    if not row or row[0] is None:
        return {"recent_form": None, "injuries": [], "data_status": "pending"}
    elo, avg = row[0], row[1]
    dc = load_dc()
    ranks = _dc_ranks(conn)
    r = ranks.get(name, {})
    return {
        "elo_gap_vs_avg": float(elo - avg) if avg is not None else None,
        "attack": float(dc.attack[name]) if dc and name in dc.attack else None,
        "defense": float(dc.defense[name]) if dc and name in dc.defense else None,
        "attack_rank": r.get("attack_rank"),
        "defense_rank": r.get("defense_rank"),
        "recent_form": None, "injuries": None,
        "data_status": "pending",
    }


# ============================================================
# 方法论(读 backtest parquet)
# ============================================================
def backtest_summary(parquet: Path = BACKTEST_PARQUET) -> dict | None:
    """三变体 accuracy/brier(128 场) + 分年 + 平局盲点分析. parquet 不存在 → None.

    平局盲点: 实际平局(actual=='D')里模型预测为平局(*_pred=='D')的比例低 → 结构性盲点
    (DC 平均预测平局率高, 但平局很少是 argmax 项). 这块诚实数据供 P1-6 「已知局限」展示.
    """
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
    # 分年准确率(2018 vs 2022) —— 2022 对 DC 特别难
    per_year: dict[str, dict] = {}
    for yr, sub in df.groupby("year"):
        per_year[str(int(yr))] = {
            v: {"accuracy": float(sub[f"{v}_hit"].mean()),
                "brier": float(sub[f"{v}_brier"].mean())}
            for v in ("elo", "dc", "dcs")}
    # 平局: 实际平局率 + 各变体 recall(预测对的比例) + 预测平局频率
    draw_mask = df["actual"] == "D"
    draw_actual_rate = float(draw_mask.mean())
    draws = df[draw_mask]
    prod = "dcs"
    limitations = {
        "draw_actual_rate": draw_actual_rate,
        "draw_recall": {v: (float((draws[f"{v}_pred"] == "D").mean()) if len(draws) else None)
                        for v in ("elo", "dc", "dcs")},
        "pred_draw_rate": {v: float((df[f"{v}_pred"] == "D").mean())
                           for v in ("elo", "dc", "dcs")},
        "prod_draw_recall": (float((draws[f"{prod}_pred"] == "D").mean())
                             if len(draws) else None),
    }
    return {
        "n_matches": int(len(df)),
        "tournaments": sorted({int(y) for y in df["year"].unique()}),
        "variants": variants,
        "per_year": per_year,
        "limitations": limitations,
        "production_variant": prod,
        "target_range": "53-55%",
    }


def calibration_rows(parquet: Path = CALIBRATION_PARQUET) -> list[dict]:
    """reliability 数据(variant×outcome×decile). parquet 不存在 → []. 用 to_json 转 numpy→py."""
    if not Path(parquet).exists():
        return []
    import pandas as pd
    df = pd.read_parquet(parquet)
    return json.loads(df.to_json(orient="records"))

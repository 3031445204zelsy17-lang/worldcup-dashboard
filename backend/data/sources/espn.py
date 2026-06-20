"""
P2-1 ESPN 非官方 API 源 —— 免费 / 无 key / 覆盖 WC2026
======================================================
scoreboard: 赛程 + 实时状态(pre/in/post) + 比分.
summary:    赛中事件流(goal/card/sub + minute) + boxscore 比分 + 当前分钟.

实测(2026-06-19): fifa.world scoreboard 返回当日赛程; summary 返回 keyEvents
(goal/yellow-card/red-card/substitution/各种 delay) + header.competitions[0] 比分.

架构
----
- parse_scoreboard(d)/parse_summary(d): 纯函数(输入 JSON dict), 单测离线喂 fixture.
- fetch_scoreboard/fetch_summary: _get(联网) + parse, 失败优雅降级返 None(参考 football_data.py).
- map_team(espn_name, known): ESPN 队名 → 模型口径(martj42 全称), 精确 dict + difflib 兜底.

非官方风险: 无 SLA / 格式可能变 / 软限流 → 优雅降级 + fixture 锁测试. 不加新依赖(复用 requests).
"""
from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger("wc.data.espn")

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
TIMEOUT = 15

# ESPN 队名 → 模型层口径(martj42 全称). 仅列与模型口径不同的; 相同的(如 Spain)原样通过.
ESPN_TEAM_MAP = {
    "Türkiye": "Turkey",
    "Czechia": "Czech Republic",
    "IR Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast",
    "Cabo Verde": "Cape Verde",
    "Republic of Ireland": "Ireland",
    "North Macedonia": "North Macedonia",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "United States": "United States",
    "South Korea": "South Korea",
}

# ESPN keyEvent type.type → 统一 game-state 事件类型(其余 delay/kickoff/halftime 忽略)
EVENT_TYPE_MAP = {
    "goal": "goal",
    "yellow-card": "card",
    "red-card": "card",
    "substitution": "sub",
}


# ============================================================
# 纯函数解析层(单测离线喂 fixture)
# ============================================================
def parse_scoreboard(d: dict) -> list[dict]:
    """ESPN scoreboard JSON → [{match_id, status_state, status_detail, home, away, home_score, away_score}]."""
    out = []
    for e in d.get("events") or []:
        c = (e.get("competitions") or [{}])[0]
        home, away, hs, as_ = _split_competitors(c.get("competitors") or [])
        stype = ((c.get("status") or {}).get("type") or {})
        out.append({
            "match_id": e.get("id"),
            "status_state": stype.get("state"),        # pre / in / post
            "status_detail": stype.get("shortDetail"),  # 'FT' / '73' '-'
            "home": home, "away": away,
            "home_score": hs, "away_score": as_,
        })
    return out


def parse_summary(d: dict) -> dict:
    """ESPN summary JSON → {status_state, home, away, home_score, away_score, minute, events[]}."""
    c = ((d.get("header") or {}).get("competitions") or [{}])[0]
    home, away, hs, as_ = _split_competitors(c.get("competitors") or [])
    stype = ((c.get("status") or {}).get("type") or {})
    status_state = stype.get("state")

    events = []
    last_minute = 0
    for ke in d.get("keyEvents") or []:
        ttype = (ke.get("type") or {}).get("type")
        etype = EVENT_TYPE_MAP.get(ttype)
        if etype is None:
            continue   # 忽略 delay/kickoff/halftime/end-regular-time 等非 game state 事件
        minute = _clock_to_minute((ke.get("clock") or {}).get("value"))
        if minute:
            last_minute = max(last_minute, minute)
        events.append({
            "type": etype,
            "is_red": ttype == "red-card",
            "team": (ke.get("team") or {}).get("displayName"),
            "player": _extract_player(ke),
            "minute": minute,
        })
    # 当前分钟: 赛中从最近事件推; pre=0; post=90(常规结束)
    minute = last_minute if status_state == "in" else (90 if status_state == "post" else 0)
    return {
        "match_id": str(d.get("header", {}).get("id", "")),
        "status_state": status_state,
        "home": home, "away": away,
        "home_score": hs, "away_score": as_,
        "minute": minute,
        "events": events,
    }


def _split_competitors(comps: list[dict]) -> tuple:
    """ESPN competitors[] → (home_name, away_name, home_score, away_score)."""
    home = away = None
    hs = as_ = None
    for comp in comps:
        name = (comp.get("team") or {}).get("displayName")
        score = _to_int(comp.get("score"))
        if comp.get("homeAway") == "home":
            home, hs = name, score
        else:
            away, as_ = name, score
    return home, away, hs, as_


def _clock_to_minute(seconds):
    """ESPN clock.value(秒) → 分钟(int(秒//60)+1). None→None. 933s→16'."""
    if seconds is None:
        return None
    return int(seconds // 60) + 1


def _extract_player(ke: dict) -> str | None:
    """从 keyEvent 提球员名: participants[0].athlete.displayName 优先, 否则 text 前缀解析."""
    parts = ke.get("participants") or []
    if parts:
        ath = (parts[0].get("athlete") or {}).get("displayName")
        if ath:
            return ath
    text = ke.get("text") or ""
    # "Sidny Cabral (Cabo Verde) is shown the yellow card..." → "Sidny Cabral"
    head = text.split("(")[0].strip()
    head = head.split(" is ")[0].strip()
    return head or None


def map_team(espn_name: str | None, known: set[str] | None = None) -> str | None:
    """ESPN 队名 → 模型口径(martj42 全称).

    精确 dict(ESPN_TEAM_MAP) → 原样(若已是模型口径, 如 Spain) → difflib 兜底(需 known 集合) → None.
    """
    if not espn_name:
        return None
    if espn_name in ESPN_TEAM_MAP:
        return ESPN_TEAM_MAP[espn_name]
    if known:
        if espn_name in known:
            return espn_name
        matches = difflib.get_close_matches(espn_name, list(known), n=1, cutoff=0.85)
        if matches:
            return matches[0]
        return None   # 未映射且不在 known → None(调用方 skip)
    return espn_name   # 无 known → 原样返回(调用方自行 validate)


# ============================================================
# 联网层(优雅降级)
# ============================================================
def _get(url: str) -> dict | None:
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("ESPN fetch 失败 %s: %s", url, e)
        return None


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_scoreboard(date: str | None = None) -> list[dict] | None:
    """date='YYYYMMDD'(None=UTC 今日). → parse_scoreboard 结果 or None(降级)."""
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
    d = _get(f"{BASE}/scoreboard?dates={date}")
    return parse_scoreboard(d) if d else None


def fetch_summary(match_id: str | int) -> dict | None:
    """→ parse_summary 结果 or None(降级)."""
    d = _get(f"{BASE}/summary?event={match_id}")
    return parse_summary(d) if d else None

"""
P1-1 数据采集层 schema: SQLite DDL + 统一比赛模型(Match/Status)
================================================================
对齐 docs/architecture.md:127-203 的 7 表 DDL —— 这里是表结构的**单一真相源**.
P1-1 只建 teams + matches(采集层写入的); events / predictions / tournament_probs /
lineups / injuries 的 DDL 在此定义但不建(由 P1-4/P2 按需初始化, 此处留全避免日后散落).

队名口径: martj42 全称(如 "United States"/"South Korea"/"Czech Republic"/"Ivory Coast"),
与 Phase 0 模型层(match_predictor / 后续 Monte Carlo)一致, 固化在
data/processed/worldcup_2026_groups.csv (48 队).

设计要点
--------
- matches 用 home_team_id/away_team_id 外键(对齐 architecture.md), 但 Match 数据类用队名字符串
  (对模型层友好); name↔id 映射只在 SQLite 边界做(cache 层).
- martj42 的 neutral 字段按【实际场地】编码(比 wc_neutral_host 按东道主身份推断更准),
  采集层透传存 matches.neutral, 供预测层直接用.
- status: upcoming(比分 NA)/ live(留 P2)/ finished(比分有值). match_key 作 upsert+diff 对齐键.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "wc.db"
DEFAULT_GROUPS_CSV = ROOT / "data" / "processed" / "worldcup_2026_groups.csv"


# ============================================================
# SQLite DDL —— 对齐 architecture.md:127-203 (单一真相源)
# ============================================================
TEAMS_DDL = """
CREATE TABLE IF NOT EXISTS teams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    group_name    TEXT,
    elo_rating    REAL,
    altitude_home REAL,
    updated_at    TIMESTAMP
)
"""

MATCHES_DDL = """
CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    home_team_id  INTEGER REFERENCES teams(id),
    away_team_id  INTEGER REFERENCES teams(id),
    score_home    INTEGER,
    score_away    INTEGER,
    status        TEXT,           -- upcoming/live/finished
    kickoff_time  TIMESTAMP,
    stage         TEXT,           -- group/ro16/qf/sf/final (本轮透传 None, 推断留 P1-3)
    neutral       INTEGER,        -- 0/1; martj42 原始场地编码, 透传给预测层
    match_key     TEXT NOT NULL UNIQUE,   -- {date}|{home}|{away} 稳定键 (upsert + diff 对齐)
    weather_json  TEXT,
    altitude      REAL,
    source        TEXT,           -- martj42 / football_data
    updated_at    TIMESTAMP
)
"""

# —— 其余 5 表: DDL 定义作真相源, P1-1 不建(P1-4 init_db(all_tables=True) 时建)——
EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER REFERENCES matches(id),
    type        TEXT,          -- goal/card/sub
    team_id     INTEGER,
    player_name TEXT,
    minute      INTEGER,
    created_at  TIMESTAMP
)
"""

PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id      INTEGER REFERENCES matches(id),
    minute        INTEGER,       -- 比赛第几分钟(0=赛前)
    home_win_prob REAL,
    draw_prob     REAL,
    away_win_prob REAL,
    model_version TEXT,          -- poisson/dixoncoles/ensemble
    confidence    REAL,
    calculated_at TIMESTAMP
)
"""

TOURNAMENT_PROBS_DDL = """
CREATE TABLE IF NOT EXISTS tournament_probs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          INTEGER REFERENCES teams(id),
    round            TEXT,       -- group/ro16/qf/sf/final
    advancement_prob REAL,
    win_prob         REAL,
    calculated_at    TIMESTAMP
)
"""

# 历史轨迹: 每次 MC 重算追加一份快照(只 INSERT 不 DELETE), 供「概率轨迹」折线 +
# 「今日变动」diff. 同一次重算的 48×6 行共用 calculated_at(快照分组键).
TOURNAMENT_PROBS_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS tournament_probs_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          INTEGER REFERENCES teams(id),
    round            TEXT,       -- group/ro16/qf/sf/final
    advancement_prob REAL,
    win_prob         REAL,
    calculated_at    TIMESTAMP
)
"""
TP_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_tph_team_time
ON tournament_probs_history(team_id, calculated_at)
"""

LINEUPS_DDL = """
CREATE TABLE IF NOT EXISTS lineups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER REFERENCES matches(id),
    team_id     INTEGER,
    players_json TEXT,
    announced_at TIMESTAMP
)
"""

INJURIES_DDL = """
CREATE TABLE IF NOT EXISTS injuries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER REFERENCES teams(id),
    player_name TEXT,
    status      TEXT,           -- injured/doubtful/fit
    updated_at  TIMESTAMP
)
"""

# P1-1 采集层建这两张; 全部表(含 history 轨迹)交给 P1-4; 索引在 init_db 单独建(非表)
P1_1_DDL = (TEAMS_DDL, MATCHES_DDL)
ALL_DDL = (TEAMS_DDL, MATCHES_DDL, EVENTS_DDL, PREDICTIONS_DDL,
           TOURNAMENT_PROBS_DDL, TOURNAMENT_PROBS_HISTORY_DDL,
           LINEUPS_DDL, INJURIES_DDL)


# ============================================================
# 比赛状态 + 统一比赛模型
# ============================================================
class Status(str, Enum):
    """比赛状态(对齐 architecture.md matches.status). str 子类便于直接写库/序列化."""
    UPCOMING = "upcoming"     # 未赛(比分 NA)
    LIVE = "live"             # 进行中(留 P2; martj42 无此态)
    FINISHED = "finished"     # 已完赛(比分有值)


@dataclass
class Match:
    """统一比赛模型(两源归一). 队名用 martj42 全称(模型层口径).

    设计: 字段用队名字符串(非 team_id), 让数据流转对模型层/P1-3 Monte Carlo 友好;
    name↔id 映射只在写 SQLite 边界做(cache.upsert_matches).
    """
    date: str                       # ISO date "2026-06-11"
    home: str
    away: str
    home_score: int | None          # None = 未赛
    away_score: int | None
    status: Status
    neutral: bool                   # martj42 原始场地编码(透传, 比东道主推断准)
    source: str                     # "martj42" / "football_data"
    stage: str | None = None        # 本轮 None(推断留 P1-3)
    kickoff: str | None = None      # ISO datetime(留 P1-4 前端用时区处理)
    extra: dict = field(default_factory=dict)   # 源特有字段(city/country 等, 元信息)

    @property
    def match_key(self) -> str:
        """稳定唯一键 {date}|{home}|{away}."""
        return f"{self.date}|{self.home}|{self.away}"

    @property
    def finished(self) -> bool:
        return self.status is Status.FINISHED

    def to_row(self) -> dict:
        """扁平化为 dict(供 cache 写 SQLite / Parquet). team_id 在 cache 层 join 填."""
        return {
            "date": self.date, "home": self.home, "away": self.away,
            "home_score": self.home_score, "away_score": self.away_score,
            "status": self.status.value, "neutral": int(self.neutral),
            "stage": self.stage, "kickoff": self.kickoff, "source": self.source,
            "match_key": self.match_key,
        }


# ============================================================
# 纯工具函数
# ============================================================
def match_key(date: str, home: str, away: str) -> str:
    """稳定唯一键(供 diff 对齐两轮采集)."""
    return f"{date}|{home}|{away}"


def parse_status(home_score: int | None, away_score: int | None) -> Status:
    """比分 → 状态. 任一为 None → upcoming; 都有值 → finished. (live 留 P2.)"""
    if home_score is None or away_score is None:
        return Status.UPCOMING
    return Status.FINISHED


def validate_team(name: str, known: set[str]) -> tuple[bool, str]:
    """校验队名落 48 队口径. 返回 (ok, warning_msg).

    未知队记 warning 但不崩 —— 防淘汰赛占位符(如 martj42 后填 "Winner Group A")
    或历史小队污染. ok=False 时调用方决定跳过还是保留.
    """
    if name in known:
        return True, ""
    return False, f"队名 {name!r} 不在 48 队口径(可能淘汰赛占位/历史小队), 视为 warning"


# ============================================================
# DB 初始化 + teams 灌入
# ============================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(path: str | Path = DEFAULT_DB, all_tables: bool = False) -> sqlite3.Connection:
    """建表并返回连接. 默认只建 teams+matches(P1-1); all_tables=True 建 7 表(P1-4)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: 读不阻塞写、写不阻塞读 —— worker(写)与 P1-4 API(读)共享本 DB 必需.
    # DB 级持久属性(设一次后续连接继承); :memory: 下 sqlite 返回 "memory"(不支持, 忽略不报错).
    conn.execute("PRAGMA journal_mode = WAL")
    for ddl in (ALL_DDL if all_tables else P1_1_DDL):
        conn.execute(ddl)
    if all_tables:
        conn.execute(TP_HISTORY_INDEX_DDL)   # history 轨迹索引(非表, 单独建)
    conn.commit()
    return conn


def seed_teams(conn: sqlite3.Connection, groups_csv: str | Path = DEFAULT_GROUPS_CSV,
               known_only: set[str] | None = None) -> int:
    """从 worldcup_2026_groups.csv 灌 48 队. 幂等(name UNIQUE → INSERT OR IGNORE + 更新 elo).

    altitude_home 本轮灌 NULL(中立场预测用不到; 高原主场修正已在 Elo 层落地, 见
    memory altitude-home-bias, 留 P2/P3 按需补 teams.altitude_home).
    返回写入/更新的行数.
    """
    df = pd.read_csv(groups_csv)
    now = _now_iso()
    rows = [(r.team, r.group, float(r.elo), None, now) for r in df.itertuples(index=False)]
    conn.executemany(
        "INSERT OR IGNORE INTO teams (name, group_name, elo_rating, altitude_home, updated_at) "
        "VALUES (?,?,?,?,?)", rows)
    # elo 可能随重算变 → 同步更新(不碰 name/group 保证幂等)
    conn.executemany(
        "UPDATE teams SET elo_rating=?, updated_at=? WHERE name=?",
        [(float(r.elo), now, r.team) for r in df.itertuples(index=False)])
    conn.commit()
    if known_only is not None:
        known_only.update(r[0] for r in rows)
    return len(rows)


def load_team_names(conn: sqlite3.Connection) -> set[str]:
    """读 teams 表所有队名(48 队口径, 供 collector 校验采集数据)."""
    return {row[0] for row in conn.execute("SELECT name FROM teams")}


def team_id_of(conn: sqlite3.Connection, name: str) -> int | None:
    """队名 → team_id(写 matches 外键用). 不存在返回 None. 48 队小表, 直查不缓存."""
    row = conn.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def known_teams(groups_csv: str | Path = DEFAULT_GROUPS_CSV) -> set[str]:
    """不依赖 DB, 直接从 csv 读 48 队口径(测试/离线校验用)."""
    return set(pd.read_csv(groups_csv)["team"])

"""
P1-1 三层缓存: 内存(TTL) → SQLite(温,持久) → Parquet(历史快照)
================================================================
对齐 architecture.md:207-233 三层缓存; 第三层用 Parquet 快照替代"打 API"
(主源是免费 CSV 无额度压力, 快照供回溯历史 + offline fallback).

语义
----
- 内存层: 进程内 dict[match_key, (Match, ts)] + 30s TTL(对齐 architecture.md:214).
  作用: P1-4 FastAPI 高频读时不重复查 SQLite.
- SQLite 层: upsert_matches(幂等, 按 match_key ON CONFLICT, status 变更即更新)+ load_matches.
  作用: 持久化, 重启不丢, P1-4 读取层.
- Parquet 层: snapshot 落 data/processed/matches_snapshot_<ts>.parquet.
  作用: 回溯历史 + offline fallback(martj42 在线抽风时, 最近快照可作兜底).

name↔id 映射: matches 表用 team_id 外键(对齐 architecture.md), 但 Match 用队名字符串
(对模型层友好) → upsert 时查 teams 得 id, load 时 JOIN teams 回 name 重建 Match.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.schema import Match, Status, team_id_of  # noqa: E402

SNAPSHOT_DIR = ROOT / "data" / "processed"
MEMORY_TTL = 30  # 秒, 对齐 architecture.md:214

# matches 读取 SQL(name 由 JOIN teams 回填; 按 kickoff 升序)
_LOAD_COLS = ("kickoff_time", "status", "neutral", "source", "stage",
              "home", "away", "score_home", "score_away", "match_key")
_LOAD_SQL = f"""
SELECT m.kickoff_time, m.status, m.neutral, m.source, m.stage,
       th.name AS home, ta.name AS away, m.score_home, m.score_away, m.match_key
FROM matches m
JOIN teams th ON th.id = m.home_team_id
JOIN teams ta ON ta.id = m.away_team_id
"""


class MatchCache:
    """三层缓存(内存 / SQLite / Parquet). 一个实例绑定一个 sqlite3.Connection."""

    def __init__(self, conn: sqlite3.Connection, snapshot_dir: Path | str = SNAPSHOT_DIR,
                 ttl: int = MEMORY_TTL) -> None:
        self.conn = conn
        self.snapshot_dir = Path(snapshot_dir)
        self.ttl = ttl
        self._mem: dict[str, tuple[Match, float]] = {}

    # ---------------- 内存层 ----------------
    def _mem_get(self, key: str) -> Match | None:
        item = self._mem.get(key)
        if item is None:
            return None
        m, ts = item
        if time.time() - ts > self.ttl:        # TTL 过期
            return None
        return m

    def _mem_put(self, m: Match) -> None:
        self._mem[m.match_key] = (m, time.time())

    def get(self, match_key: str) -> Match | None:
        """读单场: 内存(TTL)→ SQLite. 回源由 collector 编排(不在此打网络)."""
        m = self._mem_get(match_key)
        if m is not None:
            return m
        row = self.conn.execute(_LOAD_SQL + " WHERE m.match_key=?", (match_key,)).fetchone()
        if row is None:
            return None
        m = self._row_to_match(row)
        self._mem_put(m)
        return m

    def get_all(self) -> list[Match]:
        """读全部(直接查 SQLite, 不缓存全量到内存; P1-4 高频读时再加全量缓存)."""
        rows = self.conn.execute(_LOAD_SQL + " ORDER BY m.kickoff_time").fetchall()
        return [self._row_to_match(r) for r in rows]

    # ---------------- SQLite 层 ----------------
    def upsert_matches(self, matches: list[Match]) -> tuple[int, list[str]]:
        """幂等写入 matches. 按 match_key ON CONFLICT 更新(status/score 变更即覆盖).

        队名不在 teams 表(淘汰赛占位符等)→ 跳过并计入 skipped.
        返回 (written, skipped_keys).
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        written, skipped = 0, []
        for m in matches:
            hid = team_id_of(self.conn, m.home)
            aid = team_id_of(self.conn, m.away)
            if hid is None or aid is None:
                skipped.append(m.match_key)
                continue
            self.conn.execute(
                """INSERT INTO matches (home_team_id, away_team_id, score_home, score_away,
                       status, kickoff_time, stage, neutral, match_key, source, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(match_key) DO UPDATE SET
                     score_home=excluded.score_home, score_away=excluded.score_away,
                     status=excluded.status, neutral=excluded.neutral, stage=excluded.stage,
                     source=excluded.source, updated_at=excluded.updated_at""",
                (hid, aid, m.home_score, m.away_score, m.status.value,
                 m.kickoff, m.stage, int(m.neutral), m.match_key, m.source, now))
            written += 1
            self._mem_put(m)
        self.conn.commit()
        return written, skipped

    # ---------------- Parquet 快照层 ----------------
    def snapshot(self, matches: list[Match], tag: str | None = None) -> Path:
        """落 Parquet 快照 → 返回路径. 供回溯 + offline fallback.

        tag 默认用 UTC 时间戳(快照不覆盖, 保留历史). 测试可传固定 tag.
        """
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.snapshot_dir / f"matches_snapshot_{ts}.parquet"
        pd.DataFrame([m.to_row() for m in matches]).to_parquet(path, index=False)
        return path

    def load_snapshot(self, path: Path | str) -> list[Match]:
        """从 Parquet 快照恢复 list[Match](回溯/offline fallback 用)."""
        df = pd.read_parquet(path)
        out = []
        for r in df.itertuples(index=False):
            out.append(Match(
                date=str(r.date), home=r.home, away=r.away,
                home_score=(None if pd.isna(r.home_score) else int(r.home_score)),
                away_score=(None if pd.isna(r.away_score) else int(r.away_score)),
                status=Status(r.status), neutral=bool(r.neutral),
                source=r.source, stage=(None if pd.isna(r.stage) else r.stage),
                kickoff=(None if pd.isna(r.kickoff) else str(r.kickoff)),
            ))
        return out

    # ---------------- row → Match ----------------
    @staticmethod
    def _row_to_match(row) -> Match:
        kickoff, status, neutral, source, stage, home, away, hs, as_, key = row
        date = (kickoff[:10] if kickoff else key.split("|")[0])
        return Match(
            date=date, home=home, away=away,
            home_score=hs, away_score=as_,
            status=Status(status), neutral=bool(neutral),
            source=source, stage=stage, kickoff=kickoff,
        )

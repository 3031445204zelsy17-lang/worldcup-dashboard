"""
P1-1 统一调度 + diff 检测
=========================
collect_once(): 主源采集 → 入缓存 → diff(新完赛) → 备源骨架 → 返回 CollectResult.

核心交付: detect_newly_finished —— status upcoming→finished 即新完赛(也检测比分修正告警).
P1-1 只【返回】新完赛列表, 不触发重算(钩子留 P1-2 worker).

diff 约定
--------
- old.status=upcoming → new.status=finished = 新完赛(进 newly_finished).
- old.status=finished → new.status=finished 但比分变了 = 修正(记 warning, 不进 newly_finished).
- old 里没有的 key(首轮见到)= 不报新完赛 —— 避免冷启动把全部 finished 当成"新完赛"噪声.
  (首轮 DB 空 → newly_finished 恒空; 真正的"新完赛"只来自后续轮次的 upcoming→finished 翻转.)

调度节奏(poll 频率)留 P1-2 worker; 这里只做一次 collect_once 原子操作.

运行: .venv/bin/python backend/data/collector.py   (联网拉 martj42 → 灌 SQLite → 打印 diff)
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.cache import MatchCache  # noqa: E402
from backend.data.schema import (  # noqa: E402
    DEFAULT_DB, Status, init_db, known_teams, seed_teams,
)
from backend.data.sources import football_data, martj42  # noqa: E402


@dataclass
class CollectResult:
    """一次采集的汇总结果."""
    fetched: int                                  # 本轮采集到的总场次
    source: str                                   # "martj42:online" / "martj42:offline" / "none"
    newly_finished: list = field(default_factory=list)   # list[Match] upcoming→finished
    backup_status: dict = field(default_factory=dict)    # football_data 交叉验证(占位)
    warnings: list = field(default_factory=list)
    skipped: list = field(default_factory=list)          # 队名不在 teams 被跳过的 match_key

    @property
    def has_new(self) -> bool:
        return len(self.newly_finished) > 0


def _keyed(matches) -> dict:
    return {m.match_key: m for m in matches}


def detect_newly_finished(old_by_key: dict, new_by_key: dict) -> tuple[list, list[str]]:
    """纯函数: 检测新完赛 + 比分修正告警.

    输入: {match_key: Match}. 返回 (newly_finished, warnings).
    见模块 docstring 的 diff 约定.
    """
    newly, warnings = [], []
    for key, nm in new_by_key.items():
        om = old_by_key.get(key)
        if om is None:
            continue                              # 首次见到, 不报(避免冷启动噪声)
        if om.status is Status.UPCOMING and nm.status is Status.FINISHED:
            newly.append(nm)
        elif om.status is Status.FINISHED and nm.status is Status.FINISHED:
            if (om.home_score, om.away_score) != (nm.home_score, nm.away_score):
                warnings.append(
                    f"比分修正 {key}: {om.home_score}-{om.away_score} "
                    f"→ {nm.home_score}-{nm.away_score}")
    return newly, warnings


def collect_once(conn: sqlite3.Connection, allow_network: bool = True,
                 known: set[str] | None = None, fetch_backup: bool = True,
                 snapshot: bool = True) -> CollectResult:
    """采集一轮. conn 须已 init_db + seed_teams. 返回 CollectResult."""
    cache = MatchCache(conn)
    if known is None:
        known = known_teams()
    warnings: list[str] = []

    # 1) 旧状态(本轮 diff 基线)
    old_by_key = _keyed(cache.get_all())

    # 2) 主源采集
    try:
        new_matches, source = martj42.fetch_wc2026(allow_network=allow_network)
    except RuntimeError as e:
        # 在线失败且无本地副本 → 用现有 DB 兜底, 不崩
        return CollectResult(
            fetched=len(old_by_key), source="none",
            backup_status={"enabled": False, "note": str(e)},
            warnings=[f"主源不可用, 回退现有缓存: {e}"])

    # 3) 队名校验(unknown 不崩, 但 upsert 时 teams 表无对应行会跳过)
    unknown = {m.home for m in new_matches if m.home not in known} | \
              {m.away for m in new_matches if m.away not in known}
    if unknown:
        warnings.append(f"未知队名({len(unknown)} 个): {sorted(unknown)[:5]}")

    # 4) 入缓存(幂等)
    written, skipped = cache.upsert_matches(new_matches)
    if skipped:
        warnings.append(f"跳过(队名不在 teams 表): {len(skipped)} 场, 如 {skipped[:3]}")

    # 5) diff
    newly_finished, diff_warn = detect_newly_finished(old_by_key, _keyed(new_matches))
    warnings.extend(diff_warn)

    # 6) Parquet 快照(每次采集落一份, 供回溯 + offline fallback)
    if snapshot:
        try:
            cache.snapshot(new_matches)
        except Exception as e:
            warnings.append(f"快照失败(非致命): {e}")

    # 7) 备源骨架(本轮优雅降级: 无 key/不可达 → 占位)
    backup_status = (football_data.cross_verify(new_matches, football_data.fetch_results())
                     if fetch_backup else {"enabled": False, "note": "未调用"})

    return CollectResult(
        fetched=len(new_matches), source=source,
        newly_finished=newly_finished, backup_status=backup_status,
        warnings=warnings, skipped=skipped)


def _fmt_match(m) -> str:
    return f"{m.date}  {m.home} {m.home_score}-{m.away_score} {m.away}  [neutral={m.neutral}]"


def main() -> None:
    conn = init_db(DEFAULT_DB)
    seed_teams(conn)
    # 第 1 轮: 冷启动(DB 空)→ newly_finished 应为空
    r1 = collect_once(conn)
    print("=" * 62)
    print(f"第 1 轮采集   source={r1.source}")
    print(f"  fetched={r1.fetched}  newly_finished={len(r1.newly_finished)}  "
          f"(冷启动约定: 不报)  skipped={len(r1.skipped)}")
    if r1.warnings:
        print("  warnings:")
        for w in r1.warnings:
            print(f"    - {w}")
    print(f"  备源: {r1.backup_status}")
    # 第 2 轮: 幂等, 无新完赛
    r2 = collect_once(conn)
    print("-" * 62)
    print(f"第 2 轮采集   source={r2.source}")
    print(f"  fetched={r2.fetched}  newly_finished={len(r2.newly_finished)}  "
          f"(幂等, 应为 0)")
    if r2.has_new:
        print("  新完赛:")
        for m in r2.newly_finished:
            print(f"    + {_fmt_match(m)}")
    print("=" * 62)
    print(f"SQLite: {DEFAULT_DB.relative_to(ROOT)}  "
          f"(sqlite3 查: SELECT status, COUNT(*) FROM matches GROUP BY status)")


if __name__ == "__main__":
    main()

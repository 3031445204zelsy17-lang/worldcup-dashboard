"""
P1-2 后台 worker: 定时采集 + 赛后触发 Monte Carlo 重算
=====================================================
把 P1-1 采集层 + P1-3 模拟器拼成定时循环, 落实 CLAUDE.md「后台采集 + 用户只读」——
用户访问 0 次外部 API(额度与用户数解耦), 本进程后台轮询 martj42 并按赛果刷新概率.

架构(对齐 architecture.md + 延续 P1-1「零新增依赖」)
---------------------------------------------------
- 独立进程 `python -m backend.worker`, 与 P1-4 FastAPI 解耦(API 只读 DB, 不触发采集).
- 纯同步 `while` 循环 + `threading.Event` 可打断 sleep + SIGINT/SIGTERM 优雅退出.
  (拒绝 apscheduler/httpx/aiosqlite —— 同步 requests+sqlite3 足够, async 栈留 P1-4.)
- 单实例 pidfile 守卫(防 double-run 重复采集/重算, cron/重启场景必备).

数据流(单一真相源 = DB)
------------------------
- `collect_once` 把 martj42 灌进 matches 表(P1-1 幂等 upsert + diff).
- 重算时 fixtures = `cache.get_all()`(从 DB 读最新, 含刚完赛比分)→ MonteCarloSimulator
  锁定已完赛场实际比分 → 聚合晋级/夺冠概率 → 覆写 tournament_probs.
  (不依赖 martj42 本地副本刷新时序 —— DB 即真相, 比默认离线 fixtures 更准.)
- 重算只在 `newly_finished > 0` 时触发(P1-1 冷启动/幂等约定); 否则幂等跳过.
- 密集轮询不落 Parquet 快照; 改为「重算时」落一份(赛果变化点, 控制文件数 ≤ 完赛场数).

配置(.env 可覆盖; 部署平台注入的 env 优先, 见 load_dotenv)
----------------------------------------------------------
- WORKER_DB             SQLite 路径(默认 data/wc.db; 测试/多实例可隔离)
- WORKER_POLL_INTERVAL  轮询秒数(默认 300)
- WORKER_MC_N           MC 模拟次数(默认 10000)
- WORKER_ONESHOT        "1"→跑一轮退出(cron 调度)
- WORKER_ALLOW_NETWORK  "0"→离线(测试/调试)
- WORKER_PIDFILE        pidfile 路径(默认 data/worker.pid)
- WORKER_SEED           MC 随机种子(默认 None=随机; 调试可固定复现)

运行
----
- .venv/bin/python -m backend.worker            # 常驻轮询
- .venv/bin/python -m backend.worker --once     # 单轮(采集 + 条件重算, cron 用)
- .venv/bin/python -m backend.worker --dry      # 单轮离线(只看 diff, 不重算)

测试: .venv/bin/python -m unittest backend.test_worker
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.cache import MatchCache  # noqa: E402
from backend.data.collector import collect_once  # noqa: E402
from backend.data.schema import DEFAULT_DB, init_db, seed_teams  # noqa: E402
from backend.models.dixon_coles import DixonColes  # noqa: E402
from backend.simulation.mc import (  # noqa: E402
    DEFAULT_DC_JSON, DEFAULT_DC_PARQUET, DEFAULT_N, MonteCarloSimulator,
)
from backend.simulation.live_odds import DEFAULT_N as DEFAULT_LIVE_N, LiveMatchSimulator  # noqa: E402
from backend.data.sources import espn  # noqa: E402

log = logging.getLogger("wc.worker")

# P2-1 赛中高频轮询: 有 live 比赛 → 60s 跑 live_tick; 无 → 常规 interval(300s) tick
LIVE_POLL_INTERVAL = 60


# ============================================================
# 一轮: 采集 → diff → (条件)重算 + 持久化
# ============================================================
@dataclass
class TickResult:
    """一轮 worker 的结果(日志 + 测试断言用)."""
    fetched: int                          # 本轮采集场次
    source: str                           # martj42:online / martj42:offline / none
    newly_finished: int                   # 新完赛场数
    recomputed: bool                      # 是否触发 MC 重算
    probs_rows: int = 0                   # 重算写入 tournament_probs 行数
    elapsed_mc_ms: float = 0.0            # MC 重算耗时(ms)
    warnings: list = field(default_factory=list)


def tick(conn, *, collect_fn=None, dc=None, n: int = DEFAULT_N,
         allow_network: bool = True, snapshot_on_recompute: bool = True,
         seed: int | None = None) -> TickResult:
    """worker 一轮: 采集入 DB + diff → 若 newly_finished>0 则重算 MC 并持久化.

    - ``collect_fn`` 可注入(默认 collect_once), 便于测试替换采集结果.
    - 重算只在有新完赛时触发(P1-1 冷启动/幂等约定); 否则幂等返回, 不碰 MC.
    - 重算 fixtures 来自 DB(``cache.get_all``), 已完赛场锁实际比分 —— 赛中实时晋级概率语义.
    - 单次重算失败只记 warning, 不抛(常驻进程不因一轮失败而死).
    """
    collect_fn = collect_fn or collect_once
    # 采集入 DB + diff(密集轮询关快照; 改为重算时落, 避免文件爆炸)
    r = collect_fn(conn, allow_network=allow_network, fetch_backup=False, snapshot=False)
    warnings = list(r.warnings)

    if not r.has_new:
        return TickResult(fetched=r.fetched, source=r.source,
                          newly_finished=len(r.newly_finished),
                          recomputed=False, warnings=warnings)

    # 有新完赛 → 重算 MC(DB fixtures 为单一真相源)
    cache = MatchCache(conn)
    fixtures = cache.get_all()
    try:
        t0 = time.perf_counter()
        sim = MonteCarloSimulator(dc=dc, fixtures=fixtures, seed=seed)
        probs = sim.run(n=n)
        df = sim.to_dataframe(probs)
        rows = sim.save(conn, df)
        elapsed = (time.perf_counter() - t0) * 1000.0
    except Exception as e:
        warnings.append(f"MC 重算失败(本轮跳过, 不杀循环): {e}")
        return TickResult(fetched=r.fetched, source=r.source,
                          newly_finished=len(r.newly_finished),
                          recomputed=False, warnings=warnings)

    # 赛果变化点落一份快照(回溯用; 非每 tick 落)
    if snapshot_on_recompute:
        try:
            cache.snapshot(fixtures)
        except Exception as e:
            warnings.append(f"快照失败(非致命): {e}")

    return TickResult(fetched=r.fetched, source=r.source,
                      newly_finished=len(r.newly_finished),
                      recomputed=True, probs_rows=rows,
                      elapsed_mc_ms=elapsed, warnings=warnings)


# ============================================================
# P2-1 赛中一轮: ESPN live → game state → LiveMatchSimulator → predictions
# ============================================================
@dataclass
class LiveTickResult:
    """赛中一轮结果(找 live 比赛 → 实时胜率 → 写 predictions)."""
    live_matches: int                     # ESPN scoreboard 检测到的 live 场数
    updated: int                          # 成功写 predictions 的场数
    warnings: list = field(default_factory=list)


def _find_match_id(conn, home: str, away: str) -> int | None:
    """按队名后缀匹配找 matches.id(忽略 date 前缀, 容时区). match_key={date}|{home}|{away}."""
    row = conn.execute(
        "SELECT id FROM matches WHERE match_key LIKE ? LIMIT 1",
        (f"%|{home}|{away}",)).fetchone()
    return row[0] if row else None


def _count_reds(events: list[dict], home_espn: str, away_espn: str) -> tuple[int, int]:
    """从 events 累计红牌(按队). ESPN 队名口径不一致(competitors 'Cape Verde' vs events 'Cabo Verde')
    → 全部 map_team 归一再比. 返回 (home_reds, away_reds)."""
    home_norm = espn.map_team(home_espn)
    away_norm = espn.map_team(away_espn)
    hr = ar = 0
    for e in events:
        if e.get("type") == "card" and e.get("is_red"):
            t = espn.map_team(e.get("team"))
            if t == home_norm:
                hr += 1
            elif t == away_norm:
                ar += 1
    return hr, ar


def _upsert_prediction(conn, match_id: int, minute: int, probs: dict,
                       model_version: str) -> None:
    """幂等 upsert predictions(唯一约束 match_id+minute+model_version → 同分钟只留最新)."""
    from backend.data.schema import _now_iso
    conn.execute(
        "INSERT INTO predictions (match_id, minute, home_win_prob, draw_prob, "
        "away_win_prob, model_version, confidence, calculated_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(match_id, minute, model_version) DO UPDATE SET "
        "home_win_prob=excluded.home_win_prob, draw_prob=excluded.draw_prob, "
        "away_win_prob=excluded.away_win_prob, calculated_at=excluded.calculated_at",
        (match_id, minute, probs["home_win"], probs["draw"], probs["away_win"],
         model_version, None, _now_iso()))
    conn.commit()


def _update_match_live(conn, match_id: int, home_score: int, away_score: int) -> None:
    """更新 matches 的赛中比分 + status='live'(赛后 martj42 tick 覆盖为 finished)."""
    from backend.data.schema import _now_iso
    conn.execute(
        "UPDATE matches SET score_home=?, score_away=?, status='live', updated_at=? "
        "WHERE id=?", (home_score, away_score, _now_iso(), match_id))
    conn.commit()


def live_tick(conn, *, dc, fetch_scoreboard_fn=None, fetch_summary_fn=None,
              allow_network: bool = True, seed: int | None = None,
              n: int = DEFAULT_LIVE_N) -> LiveTickResult:
    """赛中一轮: 今日 ESPN scoreboard 找 status=in 比赛 → summary → LiveMatchSimulator → upsert predictions.

    - 无 live / 离线 / 降级 → updated=0, 不抛.
    - 队名映射失败或不在 DC teams → skip + warning(LiveMatchSimulator 会 KeyError).
    - matches 表无此场(ESPN 有 martj42 未采集) → skip.
    - 单场失败 try/except, 不杀整轮.
    - fetch_*_fn 可注入(测试用 fixture 替换联网).
    """
    fetch_scoreboard_fn = fetch_scoreboard_fn or espn.fetch_scoreboard
    fetch_summary_fn = fetch_summary_fn or espn.fetch_summary
    warnings: list = []
    if not allow_network:
        return LiveTickResult(live_matches=0, updated=0)

    board = fetch_scoreboard_fn()
    if not board:
        return LiveTickResult(live_matches=0, updated=0, warnings=["scoreboard 降级"])
    live = [m for m in board if m.get("status_state") == "in"]
    if not live:
        return LiveTickResult(live_matches=0, updated=0)

    known = set(getattr(dc, "teams", []) or [])
    updated = 0
    for m in live:
        try:
            summ = fetch_summary_fn(m["match_id"])
            if not summ:
                warnings.append(f"summary 降级 {m['match_id']}")
                continue
            home = espn.map_team(summ.get("home"), known=known)
            away = espn.map_team(summ.get("away"), known=known)
            if not home or not away or home not in known or away not in known:
                warnings.append(f"队名未落 DC teams: {summ.get('home')} v {summ.get('away')}")
                continue
            match_db_id = _find_match_id(conn, home, away)
            if match_db_id is None:
                warnings.append(f"matches 表无此场(跳过): {home} v {away}")
                continue
            # 红牌从 events 累计(ESPN 原始队名, _count_reds 内部归一)
            home_reds, away_reds = _count_reds(summ.get("events") or [],
                                               summ.get("home"), summ.get("away"))
            sim = LiveMatchSimulator(home, away, dc=dc, seed=seed)
            r = sim.simulate(summ.get("minute", 0),
                             summ.get("home_score") or 0, summ.get("away_score") or 0,
                             home_reds, away_reds, n=n)
            _upsert_prediction(conn, match_db_id, r["minute"], r, r["model_version"])
            _update_match_live(conn, match_db_id, r["home_score"], r["away_score"])
            updated += 1
            log.info("live 更新 %s v %s %d' %d-%d 红%d-%d → 胜%.0f%%平%.0f%%负%.0f%%",
                     home, away, r["minute"], r["home_score"], r["away_score"],
                     home_reds, away_reds,
                     r["home_win"] * 100, r["draw"] * 100, r["away_win"] * 100)
        except Exception as e:
            warnings.append(f"live 场 {m.get('match_id')} 失败(跳过): {e}")
    return LiveTickResult(live_matches=len(live), updated=updated, warnings=warnings)


# ============================================================
# 单实例 pidfile 守卫
# ============================================================
def _pid_alive(pid: int) -> bool:
    """PID 是否还在运行(用于判断 stale pidfile)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                 # 别人的进程, 保守当作活着(避免误启动第二实例)
    except OSError:
        return False
    return True


def acquire_pidfile(path) -> None:
    """单实例守卫: pidfile 在 + 其 PID 活 → 拒绝启动; 否则写入当前 PID.

    同 PID 再 acquire 视作重启覆盖自己的 stale pidfile, 放行.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = int(path.read_text().strip())
        except (ValueError, OSError):
            old = None
        if old and old != os.getpid() and _pid_alive(old):
            raise SystemExit(f"worker 已在运行(pid={old}, {path}), 退出")
    path.write_text(str(os.getpid()))


def release_pidfile(path) -> None:
    """释放 pidfile(退出时调; 缺失静默)."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


# ============================================================
# 常驻循环
# ============================================================
def run_forever(conn, *, interval: int = 300, n: int = DEFAULT_N, dc=None,
                allow_network: bool = True, pidfile=None, seed: int | None = None,
                collect_fn=None, max_ticks: int | None = None,
                live_enabled: bool = True,
                fetch_scoreboard_fn=None, fetch_summary_fn=None) -> None:
    """常驻轮询. SIGINT/SIGTERM → 优雅退出. ``max_ticks`` 限轮数(测试/调试用).

    P2-1 赛中协调: 每轮先 live_tick(ESPN live → predictions, 高频); 常规 tick(采集+MC 重算)
    按 ``interval`` 低频. 动态 sleep: 有 live → LIVE_POLL_INTERVAL(60s), 无 → interval.
    单轮 tick/live_tick 异常被兜底捕获(双保险, 不杀循环).
    """
    dc = dc or DixonColes.from_artifacts(DEFAULT_DC_PARQUET, DEFAULT_DC_JSON)
    stop = threading.Event()

    def _on_signal(signum, _frame):
        log.info("收到信号 %s, 准备退出...", signum)
        stop.set()
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    if pidfile:
        acquire_pidfile(pidfile)
    log.info("worker 启动 · interval=%ss · live=%ss(有live时) · n=%s · network=%s",
             interval, LIVE_POLL_INTERVAL, n, allow_network)
    ticks = 0
    last_tick_time = 0.0
    try:
        while not stop.is_set():
            # P2-1: live_tick 每轮(赛中高频更新 predictions)
            live_res = None
            if live_enabled:
                try:
                    live_res = live_tick(conn, dc=dc, allow_network=allow_network,
                                         fetch_scoreboard_fn=fetch_scoreboard_fn,
                                         fetch_summary_fn=fetch_summary_fn, seed=seed)
                    if live_res.live_matches or live_res.updated or live_res.warnings:
                        log.info("live_tick: live=%d updated=%d", live_res.live_matches, live_res.updated)
                        for w in live_res.warnings:
                            log.warning("  ⚠ %s", w)
                except Exception as e:                          # 兜底: live_tick 内部已处理, 双保险
                    log.exception("live_tick 异常(不杀循环): %s", e)
            # 常规 tick(采集 + 条件 MC 重算, 按 interval 低频)
            if time.time() - last_tick_time >= interval:
                try:
                    res = tick(conn, dc=dc, n=n, allow_network=allow_network,
                               collect_fn=collect_fn, seed=seed)
                    _log_tick(res)
                except Exception as e:                          # 兜底
                    log.exception("tick 异常(不杀循环): %s", e)
                last_tick_time = time.time()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                log.info("已达 max_ticks=%s, 退出", max_ticks)
                break
            # 动态 interval: 有 live → 高频; 无 → 常规 interval 剩余
            has_live = bool(live_res and live_res.live_matches)
            sleep_s = (LIVE_POLL_INTERVAL if has_live
                       else max(1, interval - int(time.time() - last_tick_time)))
            if stop.wait(sleep_s):                              # True = 被信号打断
                break
    finally:
        if pidfile:
            release_pidfile(pidfile)
        log.info("worker 已退出(共 %d 轮)", ticks)


def _log_tick(res: TickResult) -> None:
    """格式化一轮日志."""
    base = (f"tick: fetched={res.fetched} source={res.source} "
            f"newly={res.newly_finished}")
    if res.recomputed:
        log.info("%s → 重算 %d 行(%.0fms)", base, res.probs_rows, res.elapsed_mc_ms)
    else:
        log.info("%s · 无新完赛, 跳过重算", base)
    for warn in res.warnings:
        log.warning("  ⚠ %s", warn)


# ============================================================
# .env 加载(零依赖; 部署平台注入的 env 优先, 不覆盖)
# ============================================================
def load_dotenv(path=None) -> None:
    """极简 .env 加载(无 python-dotenv 依赖). 已设的 env 不覆盖(部署平台优先).

    支持 ``KEY=VALUE``、``KEY="quoted"``、``# 注释``、空行; 不处理 export 前缀/多行引号
    (够本项目用; 复杂需求再上 python-dotenv).
    """
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ============================================================
# CLI
# ============================================================
def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else list(argv)
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    interval = int(os.environ.get("WORKER_POLL_INTERVAL", "300"))
    n = int(os.environ.get("WORKER_MC_N", str(DEFAULT_N)))
    allow_network = _env_bool("WORKER_ALLOW_NETWORK", True)
    seed_env = os.environ.get("WORKER_SEED")
    seed = int(seed_env) if seed_env else None
    db_path = os.environ.get("WORKER_DB", str(DEFAULT_DB))
    pidfile = os.environ.get("WORKER_PIDFILE", str(ROOT / "data" / "worker.pid"))
    oneshot = ("--once" in argv) or _env_bool("WORKER_ONESHOT", False)
    dry = "--dry" in argv
    once_live = "--once-live" in argv       # P2-1: 单轮 live_tick(ESPN live → predictions)

    # matches + tournament_probs 都要(all_tables); seed_teams 幂等确保 48 队(外键依赖)
    conn = init_db(db_path, all_tables=True)
    seed_teams(conn)
    dc = DixonColes.from_artifacts(DEFAULT_DC_PARQUET, DEFAULT_DC_JSON)

    if once_live:                                   # P2-1: 单轮 live_tick 验证
        res = live_tick(conn, dc=dc, allow_network=allow_network, seed=seed)
        log.info("live_tick: live=%d updated=%d", res.live_matches, res.updated)
        for w in res.warnings:
            log.warning("  ⚠ %s", w)
        conn.close()
        return

    if oneshot or dry:
        net = allow_network and not dry
        res = tick(conn, dc=dc, n=n, allow_network=net, seed=seed)
        _log_tick(res)
        conn.close()
        return

    run_forever(conn, interval=interval, n=n, dc=dc,
                allow_network=allow_network, pidfile=pidfile, seed=seed)


if __name__ == "__main__":
    main()

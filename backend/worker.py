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

log = logging.getLogger("wc.worker")


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
                collect_fn=None, max_ticks: int | None = None) -> None:
    """常驻轮询. SIGINT/SIGTERM → 优雅退出. ``max_ticks`` 限轮数(测试/调试用).

    信号 handler 设 stop event → ``stop.wait(interval)`` 提前返回(True)→ 退出循环 →
    释放 pidfile. 单轮 tick 异常被兜底捕获(双保险, 不杀循环).
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
    log.info("worker 启动 · interval=%ss · n=%s · network=%s", interval, n, allow_network)
    ticks = 0
    try:
        while not stop.is_set():
            try:
                res = tick(conn, dc=dc, n=n, allow_network=allow_network,
                           collect_fn=collect_fn, seed=seed)
                _log_tick(res)
            except Exception as e:                          # 兜底: tick 内部已处理, 双保险
                log.exception("tick 异常(不杀循环): %s", e)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                log.info("已达 max_ticks=%s, 退出", max_ticks)
                break
            if stop.wait(interval):                         # True = 被信号打断
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

    # matches + tournament_probs 都要(all_tables); seed_teams 幂等确保 48 队(外键依赖)
    conn = init_db(db_path, all_tables=True)
    seed_teams(conn)
    dc = DixonColes.from_artifacts(DEFAULT_DC_PARQUET, DEFAULT_DC_JSON)

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

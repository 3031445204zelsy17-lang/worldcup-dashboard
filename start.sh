#!/usr/bin/env bash
# start.sh — Container Apps 单容器入口: worker(后台) + uvicorn(前台 PID1)
#
# 设计: uvicorn 是主进程(接 SIGTERM 优雅退出); worker 后台子进程。
# 风险: worker 进程级 fatal(OOM/segfault)退出后不自动重启(worker.run_forever 内 tick
#       异常已兜底捕获, 仅 fatal 才退)。升级路径: supervisord 或拆 worker/API 双 Container App。
set -euo pipefail

# —— 持久卷路径(Azure Files 挂载点; 首启 worker 自动 init_db+seed+collect 建 wc.db)——
export WORKER_DB="${WORKER_DB:-/mnt/data/wc.db}"
export API_DB="${API_DB:-/mnt/data/wc.db}"
export WORKER_PIDFILE="${WORKER_PIDFILE:-/mnt/data/worker.pid}"
export WORKER_ALLOW_NETWORK="${WORKER_ALLOW_NETWORK:-1}"
export WORKER_POLL_INTERVAL="${WORKER_POLL_INTERVAL:-300}"
export WORKER_MC_N="${WORKER_MC_N:-10000}"

# —— API 监听(Container Apps ingress 探活 8000)——
export API_HOST="${API_HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"

echo "[start] WORKER_DB=$WORKER_DB  API_HOST=$API_HOST  PORT=$PORT"

# 1. worker 后台(首启冷启动 ~10-30s 建 wc.db; 期间 API 端点降级: health 200/DB 不可读, 不崩)
echo "[start] launching worker in background..."
python -m backend.worker &
WORKER_PID=$!
echo "[start] worker PID=$WORKER_PID"

# 2. 捕获 SIGTERM(Container Apps 缩容/重启)转发给 worker, 优雅退出 + 释放 pidfile
trap 'echo "[start] SIGTERM received, forwarding to worker $WORKER_PID"; kill -TERM "$WORKER_PID" 2>/dev/null || true; wait "$WORKER_PID" 2>/dev/null || true; exit 0' TERM INT

# 3. uvicorn 前台(PID1 语义); --factory 调 create_app(); lifespan 加载 WCPredictor 单例
#    --proxy-headers: Container Apps ingress 是反代, 信任 X-Forwarded-* 还原 client 信息
echo "[start] launching uvicorn (foreground)..."
exec uvicorn backend.api.app:create_app --factory \
    --host "$API_HOST" --port "$PORT" \
    --proxy-headers --forwarded-allow-ips='*'

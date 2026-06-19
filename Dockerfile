# Dockerfile — worldcup-dashboard 镜像(API + 后台 worker + 前端静态, 单容器)
# 部署: Azure Container Apps(eastasia); CI(GitHub Actions) az acr build 云端 multi-stage 构建.
#   Stage 1 node build 前端 → dist; Stage 2 python 装后端 + 烤入 dist.
#   同源部署: 不设 VITE_API_BASE → 前端用相对 /api, 单域名免 CORS.

# ---- Stage 1: 前端构建 ----
FROM node:20-slim AS frontend
WORKDIR /fe
# 先装依赖(layer cache: package-lock 不变不重装)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # → /fe/dist (index.html + assets/)

# ---- Stage 2: 后端(FastAPI 只读 API + 后台 worker + 前端 SPA)----
FROM python:3.12-slim

# 系统库: matplotlib import 时探测 libGL.so.1(虽后端 Agg 不画图, import 路径仍触发动态加载);
# libglib2.0-0 是 matplotlib 传递依赖。slim 无需 gcc(全 wheel, 不编译)。
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖(利用 layer cache: requirements 不变就不重装)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 后端代码 + CI 预生成的模型 artifacts(DC/Elo/回测; gitignore 不阻止 docker COPY 本地文件)
COPY backend/ ./backend/
COPY data/processed/ ./data/processed/

# 前端构建产物(同源: uvicorn serve API + SPA, 单域名)
COPY --from=frontend /fe/dist ./frontend/dist

# 容器入口: worker(后台) + uvicorn(前台 PID1)
COPY start.sh ./start.sh
RUN chmod +x start.sh

# Container Apps ingress 探活端口; 持久卷挂载点(Azure Files → /mnt/data, wc.db 由 worker 首启自建)
EXPOSE 8000
VOLUME ["/mnt/data"]

CMD ["./start.sh"]

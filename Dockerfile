# Dockerfile — worldcup-dashboard 后端镜像 (FastAPI 只读 API + 后台 worker 双进程)
# 部署: Azure Container Apps; CI(GitHub Actions) 在 docker build 前生成 data/processed/ artifacts
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

# 容器入口: worker(后台) + uvicorn(前台 PID1)
COPY start.sh ./start.sh
RUN chmod +x start.sh

# Container Apps ingress 探活端口; 持久卷挂载点(Azure Files → /mnt/data, wc.db 由 worker 首启自建)
EXPOSE 8000
VOLUME ["/mnt/data"]

CMD ["./start.sh"]

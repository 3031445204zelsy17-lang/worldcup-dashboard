"""
P1-4 API 入口: python -m backend.api
====================================
复用 worker.load_dotenv(零依赖 .env loader, 部署平台 env 优先). 与 worker 两进程并存:
worker 后台写 DB(开 WAL), API 只读 DB.
"""
from __future__ import annotations

import logging
import os

from backend.api.app import create_app
from backend.worker import load_dotenv


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S")
    import uvicorn
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()

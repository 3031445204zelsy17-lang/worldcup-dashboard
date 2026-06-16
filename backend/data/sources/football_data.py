"""
P1-1 备数据源骨架: football-data.org (本轮: 骨架 + 优雅降级)
============================================================
决策(用户拍板「做骨架 + 优雅降级」): 本轮 football-data 只做骨架 ——
无 key / 403 / 超时 → 返回 None 不崩; 留 cross_verify 接口和队名映射表 TODO,
等激活时补全双源比分交叉验证.

为什么留备源: ① martj42 在线版偶尔抽风时兜底 ② 双源比分一致性校验(architecture.md 双源精神).
为什么不本轮做实: football-data 免费档队名用代号(如 "USA"/"Korea Republic")与 martj42 全称
不一致 → 映射有成本; 且当前 martj42 在线版很新鲜, 备源非关键路径. 激活条件见 P1-1 note.

免费层限制(已知): ~100 请求/天, ~10/分钟, 需 X-Auth-Token header.
"""
from __future__ import annotations

from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
FD_BASE = "https://api.football-data.org/v4"
FD_WC_COMP = "WC"   # competition code —— 本轮不验证, 激活时确认(可能 WC/CLI/QLF)


def load_env_key(name: str = "FOOTBALL_DATA_KEY") -> str | None:
    """从 .env 读 key(手动解析, 对齐 probe_api.py 范式, 不依赖 python-dotenv)."""
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            val = line.split("=", 1)[1].strip().strip("'\"")
            return val or None
    return None


def fetch_results(token: str | None = None, timeout: int = 20) -> dict | None:
    """拉 WC 比赛结果 JSON. 无 key / 403 / 超时 / 任意错误 → 返回 None(优雅降级).

    返回 None 是正常态(本轮备源默认不启用)—— 调用方据此跳过交叉验证, 不阻塞主链路.
    """
    if token is None:
        token = load_env_key()
    if not token:
        return None
    try:
        r = requests.get(f"{FD_BASE}/competitions/{FD_WC_COMP}/matches",
                         headers={"X-Auth-Token": token}, timeout=timeout)
        if r.status_code == 403:           # 免费层不含该 competition / token 无效
            return None
        r.raise_for_status()
        return r.json()
    except Exception:                      # 网络/超时/解析 —— 统一降级, 不崩
        return None


def cross_verify(martj42_matches: list, fd_data: dict | None) -> dict:
    """双源比分交叉验证. 骨架: 本轮记占位, 不做实际比对.

    TODO(激活备源时): ① 建 football-data 代号 ↔ martj42 全称 映射表(48 队)
    ② 对 finished 场比分做一致性校验 ③ 冲突 → 记 warning(martj42 更权威, 以它为准)
    """
    if fd_data is None:
        return {"enabled": False,
                "note": "football-data 未启用(无 key/不可达), 跳过交叉验证"}
    return {"enabled": True, "verified": 0, "mismatches": [],
            "note": "骨架: 队名映射表(FD代号↔martj42全称)待激活时补全"}

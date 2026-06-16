"""
P1-1 前置诊断: 从 .env 读 API-Football key, ping 2026 WC 数据端点.
================================================================
用途: ① 确认 key 端到端可用(不只是 /status) ② 探出 WC 数据结构供 P1-1 schema 映射.
key 只从 .env 读, 不经命令行/对话/日志.
运行: .venv/bin/python backend/data/probe_api.py
"""
import json
import os
import sys
from pathlib import Path

import requests  # 自带 certifi 证书, 免 macOS SSL 坑; P1-1 采集层也用它

ROOT = Path(__file__).resolve().parents[2]


def load_env_key() -> str | None:
    """从 .env 读 API_FOOTBALL_KEY(手动解析, 不依赖 python-dotenv)."""
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith("API_FOOTBALL_KEY="):
            val = line.split("=", 1)[1].strip().strip("'\"")
            return val or None
    return None


def api_get(path: str, key: str) -> dict:
    url = f"https://v3.football.api-sports.io{path}"
    r = requests.get(url, headers={"x-apisports-key": key}, timeout=20)
    r.raise_for_status()
    return r.json()


def main():
    key = load_env_key()
    if not key:
        sys.exit("[ERR] .env 里没找到 API_FOOTBALL_KEY。请先 cp .env.example .env 并填 key。")
    print(f"[OK] 从 .env 读到 key (前4位: {key[:4]}...)")

    # 1) 确认 WC league id
    print("\n=== leagues?search=World Cup ===")
    try:
        data = api_get("/leagues?search=World%20Cup", key)
    except Exception as e:
        sys.exit(f"[ERR] 调用失败: {e}\n(check: key 激活? 网络? 额度?)")
    if data.get("errors"):
        sys.exit(f"[ERR] API 返回 errors: {data['errors']}")
    wc = [(lg["league"]["id"], lg["league"]["name"], lg["seasons"][-1]["year"] if lg["seasons"] else None)
          for lg in data["response"] if "World" in lg["league"]["name"] and lg["league"]["type"] == "league"]
    print(f"找到 {len(wc)} 个 World Cup 相关 league:")
    for lid, name, yr in wc:
        print(f"  id={lid}  {name}  最新赛季={yr}")

    # 2) 拉 2026 WC 赛程(用 id=1, 主流是 FIFA World Cup)
    print("\n=== fixtures?league=1&season=2026 ===")
    try:
        fx = api_get("/fixtures?league=1&season=2026", key)
    except Exception as e:
        sys.exit(f"[ERR] fixtures 调用失败: {e}")
    if fx.get("errors"):
        sys.exit(f"[ERR] fixtures errors: {fx['errors']} (可能 league id 或 season 不对, 看上面 leagues 列表)")
    res = fx["response"]
    print(f"results: {fx['results']} 场")
    if res:
        # 结构探查: 第一场的字段
        s = res[0]
        print("\n--- 单场结构(字段名) ---")
        print("fixture:", list(s.get("fixture", {}).keys()))
        print("teams.home:", list(s.get("teams", {}).get("home", {}).keys()))
        print("goals:", list(s.get("goals", {}).keys()))
        print("league:", list(s.get("league", {}).keys()))
        # 抽样几场
        print("\n--- 抽样 5 场 ---")
        for m in res[:5]:
            f = m["fixture"]; t = m["teams"]; g = m["goals"]
            print(f"  {f.get('date','')[:16]}  {t['home']['name']} {g['home']}-{g['away']} {t['away']['name']}  "
                  f"[{f.get('status',{}).get('short','')}]")
        # 状态分布(已踢/未踢)
        from collections import Counter
        st = Counter(m["fixture"]["status"]["short"] for m in res)
        print(f"\n状态分布: {dict(st)} (FT=已完赛, NS=未开始, 1H/2H=进行中, 等)")

    # 3) 额度
    print("\n=== /status (剩余额度) ===")
    st = api_get("/status", key)
    print(f"  计划: {st['response']['subscription']['plan']} | "
          f"今日已用: {st['response']['requests']['current']}/{st['response']['requests']['limit_day']}")


if __name__ == "__main__":
    main()

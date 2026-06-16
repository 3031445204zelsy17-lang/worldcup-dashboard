"""
P1-1 主数据源: martj42/international_results GitHub CSV
========================================================
免费、含 2026 WC 全赛程(赛完填比分, 延迟~1 天)、队名 = 模型层口径(martj42 全称)**零映射成本**.

采集策略
--------
在线拉最新 CSV(raw.githubusercontentusercontent) → 解析 2026 WC → list[Match].
失败 fallback 到本地 data/raw/results.csv(offline 备份, 由上次成功采集刷新).
每次在线成功后刷新本地副本(让 offline 兜底跟上 martj42 的更新).

数据形态(2026-06-16 一手核实): 72 行 2026 WC 赛程, 12 行已填比分(6/11-6/14 已赛),
6/15+ 为 NA,NA(未赛). 正好赛中状态 → diff(NA→值)场景真实可验.

results.csv 字段: date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
- neutral: 按【实际场地】编码(Mexico v South Africa 在墨西哥城→FALSE; 在美非本土→TRUE),
  比 wc_neutral_host 按东道主身份推断更准 → 透传.
- 比分 "NA"/NaN = 未赛; 有值 = 已完赛. (常规+加时, 不含点球; 平局即平局.)
"""
from __future__ import annotations

import sys
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests  # 带 certifi 证书, 免 macOS SSL 坑(对齐 probe_api.py)

# 项目根加入 sys.path → 全限定包导入(直接跑脚本 / python -m unittest 都兼容)
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.schema import Match, parse_status  # noqa: E402
MARTJ42_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
LOCAL_COPY = ROOT / "data" / "raw" / "results.csv"
WC_TOURNAMENT = "FIFA World Cup"
WC_YEAR = 2026


def fetch_csv(allow_network: bool = True, timeout: int = 20,
              retries: int = 2) -> tuple[pd.DataFrame, str]:
    """拉 martj42 全量 CSV. 返回 (df, source).

    source ∈ {"martj42:online", "martj42:offline"}.
    在线成功后刷新 LOCAL_COPY; 在线失败(用尽重试)→ fallback 本地副本;
    本地也没有 → 抛 RuntimeError(调用方决定降级).
    """
    df: pd.DataFrame | None = None
    last_err: Exception | None = None
    if allow_network:
        for attempt in range(retries + 1):       # 0..retries → 共 retries+1 次
            try:
                r = requests.get(MARTJ42_URL, timeout=timeout)
                r.raise_for_status()
                df = pd.read_csv(StringIO(r.text), parse_dates=["date"])
                # 刷新本地副本(offline 兜底跟上 martj42 更新)
                LOCAL_COPY.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(LOCAL_COPY, index=False)
                return df, "martj42:online"
            except Exception as e:                # 网络/HTTP/解析 任意失败
                last_err = e
                if attempt < retries:
                    time.sleep(1 * (attempt + 1))  # 简单退避 1s, 2s
    # 用尽在线 → fallback 本地
    if df is None:
        if not LOCAL_COPY.exists():
            raise RuntimeError(
                f"martj42 在线拉取失败且无本地副本({LOCAL_COPY}): {last_err}")
        df = pd.read_csv(LOCAL_COPY, parse_dates=["date"])
    return df, "martj42:offline"


def parse_wc2026(df: pd.DataFrame, source: str = "martj42") -> list:
    """从全量 df 筛 2026 FIFA World Cup → list[Match]. 透传 neutral; 按比分 NA 定 status."""
    df = df.copy()
    for col in ("home_score", "away_score"):
        df[col] = pd.to_numeric(df[col], errors="coerce")   # "NA"/NaN → NaN
    wc = df[(df["tournament"] == WC_TOURNAMENT) & (df["date"].dt.year == WC_YEAR)]

    matches: list[Match] = []
    for r in wc.itertuples(index=False):
        hs = None if pd.isna(r.home_score) else int(r.home_score)
        as_ = None if pd.isna(r.away_score) else int(r.away_score)
        matches.append(Match(
            date=r.date.strftime("%Y-%m-%d"),
            home=r.home_team, away=r.away_team,
            home_score=hs, away_score=as_,
            status=parse_status(hs, as_),
            neutral=bool(r.neutral),
            source=source,
            kickoff=r.date.isoformat(),
            extra={"city": getattr(r, "city", None), "country": getattr(r, "country", None)},
        ))
    return matches


def fetch_wc2026(allow_network: bool = True) -> tuple[list, str]:
    """组合: 在线/本地拉 CSV → 解析 2026 WC. 返回 (matches, source)."""
    df, src = fetch_csv(allow_network=allow_network)
    return parse_wc2026(df, source=src), src


if __name__ == "__main__":
    # 自检: 离线跑本地副本, 打印 2026 WC 概览
    matches, src = fetch_wc2026(allow_network=False)
    finished = [m for m in matches if m.finished]
    print("=" * 60)
    print(f"martj42 自检(源: {src})")
    print("=" * 60)
    print(f"2026 WC 总场次:   {len(matches)}")
    print(f"  已完赛(finished): {len(finished)}")
    print(f"  未赛(upcoming):   {len(matches) - len(finished)}")
    print("-" * 60)
    print("已完赛抽样(前 6):")
    for m in finished[:6]:
        print(f"  {m.date}  {m.home} {m.home_score}-{m.away_score} {m.away}  "
              f"[neutral={m.neutral}]")
    print("=" * 60)

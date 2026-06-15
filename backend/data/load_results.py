"""
P0-2 历史数据加载与清洗
==========================================
输入: data/raw/results.csv  (来源 martj42/international_results, 1872-2026 国际赛事)
产出: data/processed/
  - international_history.parquet   全量已完赛国际赛(按日期升序) → Elo/Dixon-Coles 训练源
  - worldcup_2018_2022.parquet      2018/2022 正赛世界杯        → 回测测试集

设计说明
--------
1. 训练集落"全量历史"而非"仅4年":
   "只用4年数据"是建模窗口决策(project-context), 由 Dixon-Coles 时间衰减实现,
   不在数据层焊死——让模型层(P0-3/P0-4)灵活选窗口, 也支持回测做 walk-forward.
2. 数据泄露防范(留给 P0-7 回测脚本执行):
   回测 2022 WC 每场时, 用"该场赛前"的比赛重算 Elo, 不能让训练集吃到测试集比赛.
3. home_score/away_score 含 NA = 未赛(2026 赛程); 训练/回测只用已完赛(dropna).
4. 比分为常规+加时, 不含点球; 平局即平局. 点球胜者另在 shootouts.csv, 留待晋级判定用.
5. neutral: TRUE=中立场地(世界杯多为中立), 主场优势建模用.

运行: .venv/bin/python backend/data/load_results.py
"""
import sys
from pathlib import Path

import pandas as pd

# 由脚本位置反推项目根, 与运行目录无关
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "results.csv"
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

if not RAW.exists():
    sys.exit(f"[ERR] 找不到 {RAW}，请先拉取 martj42/international_results 数据到 data/raw/")

# ---- 读取 ----
df = pd.read_csv(RAW, parse_dates=["date"])

# 分数转数值: 未来场次原为 "NA" 字符串 → NaN
for col in ("home_score", "away_score"):
    df[col] = pd.to_numeric(df[col], errors="coerce")
df["neutral"] = df["neutral"].astype(bool)
df["year"] = df["date"].dt.year

# ---- 已完赛 = 两队比分都非空 ----
played = df.dropna(subset=["home_score", "away_score"]).copy()
played[["home_score", "away_score"]] = played[["home_score", "away_score"]].astype(int)
played = played.sort_values("date").reset_index(drop=True)

# ---- 产出 1: 全量已完赛国际赛(训练源) ----
played.to_parquet(OUT / "international_history.parquet", index=False)

# ---- 产出 2: 2018/2022 正赛世界杯(回测集) ----
wc = played[
    (played["tournament"] == "FIFA World Cup") & (played["year"].isin([2018, 2022]))
].copy()
wc.to_parquet(OUT / "worldcup_2018_2022.parquet", index=False)

# ---- 概览 ----
future = df[df["home_score"].isna() | df["away_score"].isna()]
wc2026_sched = future[future["tournament"] == "FIFA World Cup"]
teams = set(played["home_team"]) | set(played["away_team"])

print("=" * 62)
print("P0-2 数据加载完成")
print("=" * 62)
print(f"原始行数:                {len(df):>8}")
print(f"已完赛(dropna):          {len(played):>8}  → international_history.parquet")
print(f"  涉及球队数:            {len(teams):>8}")
print(f"  日期范围:              {played['date'].min().date()} ~ {played['date'].max().date()}")
print(f"  2022起(4年窗口)场次:   {len(played[played['date'] >= '2022-01-01']):>8}")
print(f"  2018起(8年窗口)场次:   {len(played[played['date'] >= '2018-01-01']):>8}")
print(f"2018+2022 世界杯:        {len(wc):>8}  → worldcup_2018_2022.parquet")
print(f"  - 2018: {len(wc[wc['year'] == 2018])} 场 | 2022: {len(wc[wc['year'] == 2022])} 场")
print(f"2026 WC 赛程(未赛,含NA): {len(wc2026_sched):>8}  (非权威, P1 以 API-Football 为准)")
print("-" * 62)
print("已完赛 · 赛事类型 Top10:")
print(played["tournament"].value_counts().head(10).to_string())
print("-" * 62)
print("已完赛 · 按 decade:")
decade_counts = played.groupby((played["year"] // 10) * 10).size()
print(decade_counts.to_string())
print("=" * 62)

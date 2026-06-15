"""
2026 世界杯 48 支参赛队 Elo 实力榜
==========================================
来源:
  分组 = Wikipedia "2026 FIFA World Cup draw" (抽签 2025-12-05; playoff 2026-03-26/31 确定)
  Elo  = data/processed/elo_current.parquet (P0-3 产出, 含高原主场修正)
产出:
  data/processed/worldcup_2026_groups.csv  (48 队 + 分组 + Elo + 全球排名)
用途:
  - P1-3 Monte Carlo 锦标赛模拟器的参赛队/分组输入
  - 也是验收 P0-3 Elo 合理性的最佳视角(只看参赛队, 对齐项目目的)

设计说明
--------
1. 48 队分组是网络来源的硬编码事实(抽签结果), 不从 API 拉——世界杯已开赛, 名单冻结.
   若未来分组有变, 改这里即可.
2. 队名拼写以数据集(martj42/international_results)为准: Czech Republic(非 Czechia)、
   Ivory Coast(非 Côte d'Ivoire)、South Korea、United States 等.
3. Elo 实力榜 ≠ 夺冠概率榜: 夺冠概率要 P1-3 Monte Carlo 模拟整个赛程(分组+淘汰赛路径).

运行: .venv/bin/python backend/data/worldcup_2026_groups.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ELO = ROOT / "data" / "processed" / "elo_current.parquet"
OUT = ROOT / "data" / "processed" / "worldcup_2026_groups.csv"

# 2026 世界杯分组 (Wikipedia 2026 FIFA World Cup draw, 2025-12-05)
GROUPS = {
    "A": ["Mexico", "South Africa", "South Korea", "Czech Republic"],
    "B": ["Canada", "Bosnia and Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["United States", "Paraguay", "Australia", "Turkey"],
    "E": ["Germany", "Curaçao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "DR Congo", "Uzbekistan", "Colombia"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}


def build() -> pd.DataFrame:
    """读 Elo, 按 GROUPS join → 48 队实力榜 DataFrame(wc_rank, group, team, elo, global_rank)."""
    if not ELO.exists():
        sys.exit(f"[ERR] 找不到 {ELO}，请先跑 P0-3 (backend/models/elo.py)")

    elo = pd.read_parquet(ELO).reset_index(drop=True)
    elo["global_rank"] = elo.index + 1
    by_team = dict(zip(elo["team"], zip(elo["global_rank"], elo["elo"])))

    rows = []
    for g, teams in GROUPS.items():
        for t in teams:
            if t not in by_team:
                sys.exit(f"[ERR] 参赛队 {t!r} 未在 Elo 数据中匹配到，检查拼写/别名")
            gr, e = by_team[t]
            rows.append({"group": g, "team": t, "elo": round(float(e), 1), "global_rank": int(gr)})

    df = pd.DataFrame(rows).sort_values("elo", ascending=False).reset_index(drop=True)
    df.insert(0, "wc_rank", df.index + 1)
    return df


if __name__ == "__main__":
    df = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)

    print("=" * 58)
    print(f"2026 世界杯参赛队 Elo 实力榜 | {len(df)} 队 (含高原修正)")
    print(f"来源: 分组=Wikipedia draw 2025-12-05 | Elo=elo_current.parquet")
    print("=" * 58)
    print(df.to_string(index=False))

    # 死亡之组: 组内最弱队(min)越强 → 该组越难打
    grp = df.groupby("group")["elo"].agg(["mean", "min", "max"]).round(0)
    grp = grp.sort_values("min", ascending=False)
    print("-" * 58)
    print("死亡之组分析 (按组内最弱队强度降序):")
    print(grp.to_string())
    print("-" * 58)
    print(f"已存: {OUT.relative_to(ROOT)}")
    print("=" * 58)

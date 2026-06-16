"""
P0-8 评估指标: 校准 + Brier 分拆 + 平局低估分析
================================================================
只读 P0-7 的 backtest_2018_2022.parquet, 不重拟合:
  1. 校准曲线(decile reliability): 每变体 × {H/D/A}, 预测分桶 vs 实际频率, 算 ECE
  2. Brier/准确率分拆: 按年(2018/2022) + 按实际结果的 recall(暴露平局盲点)
  3. 平局低估分析 + 乘性 draw-boost 扫描: 找最优 k, 看 DC 能否被救回(关键: 若 DC 输
     Elo 主要因平局系统性低估可修, 则 draw-boost 后 DC 可能追上 Elo)
  4. verdict: 校准贴对角线? 撑不撑得起? → P0-9 输入

校准数据存 backtest_calibration.parquet(供 P1-5 前端画 reliability 图).
运行: .venv/bin/python backend/models/evaluate.py
测试: .venv/bin/python -m unittest backend.models.test_evaluate
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]
BACKTEST = ROOT / "data" / "processed" / "backtest_2018_2022.parquet"
OUT_CALIB = ROOT / "data" / "processed" / "backtest_calibration.parquet"

VARIANTS = [("elo", "A.纯Elo"), ("dc", "B.DC生产"), ("dcs", "C.DC+收缩")]
PROB_COL = {"H": "home_win", "D": "draw", "A": "away_win"}
OUTCOMES = ["H", "D", "A"]


def prob_col(variant: str, outcome: str) -> str:
    return f"{variant}_{PROB_COL[outcome]}"


# ============================================================
# 校准 (reliability)
# ============================================================
def reliability(df: pd.DataFrame, variant: str, outcome: str, n_bins: int = 10):
    """预测 P(outcome) 分桶 vs 实际 outcome 频率. 返回 (DataFrame, ECE)."""
    p = df[prob_col(variant, outcome)].to_numpy(float)
    actual = (df["actual"] == outcome).astype(float).to_numpy()
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p <= hi if i == n_bins - 1 else p < hi)
        if mask.sum() == 0:
            continue
        rows.append({"bin_lo": lo, "bin_hi": hi, "bin_center": (lo + hi) / 2,
                     "pred_mean": float(p[mask].mean()),
                     "actual_freq": float(actual[mask].mean()),
                     "count": int(mask.sum())})
    rd = pd.DataFrame(rows)
    if len(rd):
        ece = float((rd["count"] / len(df) * (rd["pred_mean"] - rd["actual_freq"]).abs()).sum())
    else:
        ece = float("nan")
    return rd, ece


# ============================================================
# Brier / 准确率分拆
# ============================================================
def accuracy_brier(df: pd.DataFrame, variant: str) -> dict:
    return {
        "accuracy": float(df[f"{variant}_hit"].mean()),
        "brier": float(df[f"{variant}_brier"].mean()),
    }


def recall_by_outcome(df: pd.DataFrame, variant: str) -> dict:
    """实际为 X 时, 预测 X 的比例(per-outcome recall). 暴露平局盲点."""
    rec = {}
    for o in OUTCOMES:
        sub = df[df["actual"] == o]
        rec[o] = float((sub[f"{variant}_pred"] == o).mean()) if len(sub) else float("nan")
    return rec


# ============================================================
# 平局乘性 boost 扫描
# ============================================================
def brier_with_draw_boost(df: pd.DataFrame, variant: str, k: float) -> float:
    """把 draw 概率 ×k 后重归一, 算多类 Brier. k=1 复现基线."""
    pH = df[f"{variant}_home_win"].to_numpy(float)
    pD = df[f"{variant}_draw"].to_numpy(float)
    pA = df[f"{variant}_away_win"].to_numpy(float)
    s = pH + k * pD + pA
    pHn, pDn, pAn = pH / s, k * pD / s, pA / s
    o = df["actual"].to_numpy()
    oH = (o == "H").astype(float)
    oD = (o == "D").astype(float)
    oA = (o == "A").astype(float)
    return float(((pHn - oH) ** 2 + (pDn - oD) ** 2 + (pAn - oA) ** 2).mean())


def draw_boost_scan(df: pd.DataFrame, variant: str,
                    ks: np.ndarray = np.arange(1.0, 6.01, 0.1)) -> dict:
    """扫描 draw-boost 因子 k, 找 Brier 最小者. 返回 {best_k, best_brier, base_brier, curve}."""
    brib = np.array([brier_with_draw_boost(df, variant, k) for k in ks])
    base = brier_with_draw_boost(df, variant, 1.0)
    i = int(brib.argmin())
    return {"best_k": float(ks[i]), "best_brier": float(brib[i]),
            "base_brier": float(base), "ks": ks, "briers": brib}


# ============================================================
# 主报告
# ============================================================
def main():
    df = pd.read_parquet(BACKTEST)
    n = len(df)
    print("=" * 80)
    print(f"P0-8 评估 | {n} 场(2018+2022 WC) | 实际分布 "
          f"H={int((df.actual=='H').sum())} D={int((df.actual=='D').sum())} "
          f"A={int((df.actual=='A').sum())} (平局率 {(df.actual=='D').mean():.1%})")
    print("=" * 80)

    # —— 1. Brier/准确率 + 分拆 ——
    print("\n[1] 准确率 / Brier(整体 + 按年 + per-outcome recall)")
    print(f"{'变体':<12}{'整体acc':>9}{'整体Brier':>11}{'2018acc':>9}{'2022acc':>9}"
          f"{'recall_H':>10}{'recall_D':>10}{'recall_A':>10}")
    for v, label in VARIANTS:
        ab = accuracy_brier(df, v)
        y18 = accuracy_brier(df[df.year == 2018], v)["accuracy"]
        y22 = accuracy_brier(df[df.year == 2022], v)["accuracy"]
        rec = recall_by_outcome(df, v)
        print(f"{label:<12}{ab['accuracy']:>8.1%}{ab['brier']:>11.4f}{y18:>9.1%}{y22:>9.1%}"
              f"{rec['H']:>10.1%}{rec['D']:>10.1%}{rec['A']:>10.1%}")
    print("  recall_D 低 = 实际平局时几乎从没预测平局(平局盲点)")

    # —— 2. 校准 (ECE) ——
    print("\n[2] 校准 ECE(越小越贴对角线; 分 H/D/A 三类)")
    calib_rows = []
    print(f"{'变体':<12}{'ECE_H':>9}{'ECE_D':>9}{'ECE_A':>9}{'ECE_均值':>10}")
    ece_summary = {}
    for v, label in VARIANTS:
        eces = {}
        for o in OUTCOMES:
            rd, ece = reliability(df, v, o)
            eces[o] = ece
            for _, r in rd.iterrows():
                calib_rows.append({"variant": v, "outcome": o, **r.to_dict()})
        mean_ece = float(np.mean(list(eces.values())))
        ece_summary[v] = mean_ece
        print(f"{label:<12}{eces['H']:>9.3f}{eces['D']:>9.3f}{eces['A']:>9.3f}{mean_ece:>10.3f}")
    # 存校准数据供 P1-5 画图
    pd.DataFrame(calib_rows).to_parquet(OUT_CALIB, index=False)
    print(f"  (校准曲线数据已存 {OUT_CALIB.name})")

    # —— 3. 平局 draw-boost 扫描 ——
    print("\n[3] 平局乘性 boost 扫描(draw 概率 ×k 重归一, 看 Brier 能降多少)")
    print(f"{'变体':<12}{'基线Brier':>11}{'最优k':>8}{'boost后Brier':>14}{'降幅':>9}")
    for v, label in VARIANTS:
        if v == "elo":
            continue   # 纯Elo 平局≈0, boost 无意义
        res = draw_boost_scan(df, v)
        drop = res["base_brier"] - res["best_brier"]
        print(f"{label:<12}{res['base_brier']:>11.4f}{res['best_k']:>8.1f}"
              f"{res['best_brier']:>14.4f}{drop:>9.4f}")

    # —— 4. verdict ——
    print("\n" + "=" * 80)
    print("[4] verdict")
    best_v = min(VARIANTS, key=lambda x: accuracy_brier(df, x[0])["brier"])
    print(f"  · Brier 最优变体: {best_v[1]} ({accuracy_brier(df, best_v[0])['brier']:.4f})")
    print(f"  · 校准最优(均值ECE最小): {min(ece_summary, key=ece_summary.get)} "
          f"(ECE={min(ece_summary.values()):.3f})")
    # 平局盲点量化
    dc_rec_d = recall_by_outcome(df, "dc")["D"]
    print(f"  · DC 平局 recall={dc_rec_d:.1%}(实际平局时预测对的占比) → 系统性盲点")
    # draw-boost 救援效果
    dc_boost = draw_boost_scan(df, "dc")
    elo_brier = accuracy_brier(df, "elo")["brier"]
    if dc_boost["best_brier"] < elo_brier:
        print(f"  · ⚠️ DC + draw-boost(k={dc_boost['best_k']:.1f}) Brier={dc_boost['best_brier']:.4f} "
              f"< 纯Elo {elo_brier:.4f} → 平局修正后 DC 可超越 Elo! 重塑 P0-7 结论")
    else:
        print(f"  · DC + draw-boost 仍劣于纯Elo({dc_boost['best_brier']:.4f} vs {elo_brier:.4f}) "
              f"→ 平局修正有帮助但不足以翻盘, κ 细扫(Part B)仍需")
    print("  · P0-9 输入: 校准曲线是否贴对角线 / 准确率~50-55% 撑得起透明定位 / 收缩+平局修正方向")
    print("=" * 80)


if __name__ == "__main__":
    main()

"""
2026 世界杯前向验证(快速版, P0-7 完整回测的先行探针)
==========================================================
思路: 数据集已含 12 场 2026 真实比赛(6/11–6/14). 用「赛前」模型预测它们,
再和真实比分对比 → 真正的 out-of-sample 验证, 比历史回测更贴近产品场景.

⚠️ 防泄露(关键): 这 12 场已在训练数据里, 绝不能用全量 fit 去"预测"它们(=偷看答案).
本脚本用 fit_at(as_of=6/11) 的赛前快照, 严格排除这 12 场 → 全部 out-of-sample.

指标:
  - 准确率: top outcome(胜/平/负最高者)是否 == 实际. 随机基线 33%.
  - 多类 Brier: Σ(p_k − o_k)², o 为实际结果 one-hot. 完美=0, 随机基线≈0.667.

历史: 本脚本首次运行即证伪了 γ_host(赛前拟合=0, 全量非零属隐性泄露)→ P0-4 据此砍掉 γ_host.
现 γ_host 恒 0, 东道主优势由 γ(主场)+队参数(实力)体现.

注: 快速版用单一赛前快照(6/11)预测全部 12 场, 不逐场 walk-forward(放弃"用同届
累积结果更新"). 完整逐场 walk-forward + Brier/校准曲线留 P0-7.

运行: .venv/bin/python backend/models/forward_validate_2026.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dixon_coles import DixonColes  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data" / "processed" / "international_history.parquet"


def outcome(h: int, a: int) -> str:
    return "H" if h > a else ("D" if h == a else "A")


def brier_multiclass(pH: float, pD: float, pA: float, actual: str) -> float:
    oH, oD, oA = (1.0 if actual == k else 0.0 for k in "HDA")
    return (pH - oH) ** 2 + (pD - oD) ** 2 + (pA - oA) ** 2


def predict_round(dc: DixonColes, wc26: pd.DataFrame) -> pd.DataFrame:
    """用给定 DC 模型预测 12 场, 返回逐场结果表."""
    rows = []
    for _, m in wc26.iterrows():
        neutral = bool(m["neutral"])
        host = (m["home_team"] == m["country"])   # 东道主: 本国作战(neutral=False)
        p = dc.predict(m["home_team"], m["away_team"],
                       neutral=neutral, host_home=host)
        probs = {"H": p["home_win"], "D": p["draw"], "A": p["away_win"]}
        actual = outcome(int(m["home_score"]), int(m["away_score"]))
        top = max(probs, key=probs.get)
        rows.append({
            "date": m["date"].date(),
            "match": f"{m['home_team']} v {m['away_team']}",
            "real": f"{int(m['home_score'])}-{int(m['away_score'])}",
            "pH": probs["H"], "pD": probs["D"], "pA": probs["A"],
            "pred": top, "actual": actual,
            "hit": "✓" if top == actual else "✗",
            "brier": brier_multiclass(probs["H"], probs["D"], probs["A"], actual),
            "host": "🏠" if host else "",
        })
    return pd.DataFrame(rows)


def main() -> None:
    df = pd.read_parquet(HIST)
    wc26 = df[(df["tournament"] == "FIFA World Cup") & (df["year"] == 2026)].sort_values("date")

    AS_OF = wc26["date"].min()   # 2026-06-11: 排除全部 12 场 → 赛前基线
    print(f"前向验证: {len(wc26)} 场 2026 比赛, 赛前快照 as_of = {AS_OF.date()}")
    print(f"训练数据 = date < {AS_OF.date()} 的 {len(df[df['date'] < AS_OF])} 场(严格排除这 12 场)\n")

    # —— 完整 DC(γ / γ_host / ρ 全开) ——
    print("拟合赛前 DC 快照…")
    dc_full = DixonColes().fit_at(df, AS_OF)
    print(f"  赛前参数: μ={dc_full.mu:.3f} γ={dc_full.gamma:.3f} "
          f"γ_host={dc_full.gamma_host:.3f} ρ={dc_full.rho:.3f}\n")

    r = predict_round(dc_full, wc26)
    acc = r["hit"].eq("✓").mean()
    mean_brier = r["brier"].mean()

    print("=" * 96)
    print("逐场预测(完整 DC, 赛前快照):")
    print("=" * 96)
    print(r[["date", "match", "real", "pH", "pD", "pA", "pred", "actual", "hit", "host"]]
          .to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("-" * 96)
    print(f"准确率: {acc:.1%} ({r['hit'].eq('✓').sum()}/{len(r)})   "
          f"随机基线 33%")
    print(f"平均 Brier: {mean_brier:.4f}   随机基线 ≈ 0.667 (越低越好, 0=完美)")
    print("=" * 96)

    # —— 东道主 3 场(享普通主场 γ; γ_host 已砍) ——
    host_rows = r[r["host"] == "🏠"]
    print(f"\n东道主场子集({len(host_rows)} 场, 享普通主场 γ):")
    print(host_rows[["match", "real", "pH", "pD", "pA", "pred", "actual", "hit"]].to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # —— 冷门点评: 模型给低概率(<25%)但实际发生的结果 ——
    r["p_actual"] = r.apply(lambda x: x["pH"] if x["actual"] == "H" else (x["pD"] if x["actual"] == "D" else x["pA"]), axis=1)
    upsets = r[(r["p_actual"] < 0.30) & (r["hit"] == "✗")].sort_values("p_actual")
    print(f"\n冷门(模型给实际结果<30% 且预测错的, {len(upsets)} 场):")
    if len(upsets):
        print(upsets[["match", "real", "pH", "pD", "pA", "actual", "p_actual"]].to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    else:
        print("  (无)")
    print("=" * 96)


if __name__ == "__main__":
    main()

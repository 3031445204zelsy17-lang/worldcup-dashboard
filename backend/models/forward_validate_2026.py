"""
2026 世界杯前向验证(P0-5: 含金量加权 + Elo 先验收缩 的效果对比)
=================================================================
思路: 数据集已含 12 场 2026 真实比赛(6/11–6/14). 用「赛前」模型预测它们, 再和真实比分对比
→ 真正的 out-of-sample 验证, 比历史回测更贴近产品场景.

⚠️ 防泄露(关键): 这 12 场已在训练数据里, 绝不能用全量 fit 去"预测"它们(=偷看答案).
本脚本用 fit_at(as_of=6/11) 的赛前快照, 严格排除这 12 场 → 全部 out-of-sample.
Elo 同样用 ratings_at(6/11) 的赛前快照做收缩先验, 时点对齐.

P0-5 三路消融对比(同一个 12 场, 看每个机制的边际贡献):
  A. P0-4 基线   : 无含金量加权(imp=1) + 无收缩       —— 复现 P0-4 结果
  B. +含金量     : 友谊赛/邀请杯 0.25, Olympic/业余剔除 + 无收缩
  C. P0-5 完整   : 含金量加权 + Elo 先验收缩(κ=5)     —— 稀疏队/友谊赛水分回归先验

指标:
  - 准确率: top outcome(胜/平/负最高者)是否 == 实际. 随机基线 33%.
  - 多类 Brier: Σ(p_k − o_k)², o 为实际结果 one-hot. 完美=0, 随机基线≈0.667.

历史: 本脚本曾证伪 γ_host(赛前拟合=0, 全量非零属隐性泄露)→ P0-4 砍掉 γ_host.
P0-5 在此基础上加含金量+收缩, 看是否修好 Canada 类(主场友谊赛水分→过度自信)错场.

注: 快速版用单一赛前快照(6/11)预测全部 12 场, 不逐场 walk-forward. 完整逐场 + Brier/校准留 P0-7.

运行: .venv/bin/python backend/models/forward_validate_2026.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dixon_coles import DixonColes  # noqa: E402
from elo import EloModel  # noqa: E402

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


def summary(r: pd.DataFrame) -> tuple[float, float]:
    """(准确率, 平均 Brier)."""
    return float(r["hit"].eq("✓").mean()), float(r["brier"].mean())


def main() -> None:
    df = pd.read_parquet(HIST)
    wc26 = df[(df["tournament"] == "FIFA World Cup") & (df["year"] == 2026)].sort_values("date")

    AS_OF = wc26["date"].min()   # 2026-06-11: 排除全部 12 场 → 赛前基线
    print(f"前向验证: {len(wc26)} 场 2026 比赛, 赛前快照 as_of = {AS_OF.date()}")
    print(f"训练数据 = date < {AS_OF.date()} 的 {len(df[df['date'] < AS_OF])} 场(严格排除这 12 场)")
    print(f"Elo 先验 = ratings_at({AS_OF.date()}) 赛前快照(时点对齐, 防泄露)\n")

    elo_at = EloModel().ratings_at(df, AS_OF)   # 赛前 Elo 快照, 喂收缩

    # —— 三路拟合(同一赛前快照) ——
    print("拟合三路赛前模型…")
    dc_A = DixonColes(use_importance_weight=False).fit_at(df, AS_OF)             # P0-4 基线
    dc_B = DixonColes(use_importance_weight=True).fit_at(df, AS_OF)              # +含金量
    dc_C = DixonColes(use_importance_weight=True).fit_at(df, AS_OF, elo=elo_at)  # P0-5 完整

    rA = predict_round(dc_A, wc26)
    rB = predict_round(dc_B, wc26)
    rC = predict_round(dc_C, wc26)

    # —— 三路汇总 ——
    print("=" * 96)
    print("三路消融对比(同一 12 场赛前预测):")
    print("=" * 96)
    print(f"{'变体':<22}{'准确率':>10}{'正确':>8}{'平均Brier':>12}   {'全局参数'}")
    for label, dc, r in [("A. P0-4 基线", dc_A, rA),
                         ("B. +含金量加权", dc_B, rB),
                         ("C. P0-5 完整(+收缩)", dc_C, rC)]:
        acc, bri = summary(r)
        n_hit = int(r["hit"].eq("✓").sum())
        print(f"{label:<22}{acc:>9.1%}{n_hit:>5}/12{bri:>12.4f}   "
              f"μ={dc.mu:.3f} γ={dc.gamma:.3f} ρ={dc.rho:.3f}")
    print(f"{'(随机基线)':<22}{'33.0%':>10}{'':>8}{'0.667':>12}")
    print("-" * 96)

    # —— P0-5 完整版逐场 ——
    print("\nP0-5 完整版逐场预测:")
    print(rC[["date", "match", "real", "pH", "pD", "pA", "pred", "actual", "hit", "host"]]
          .to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # —— 三路逐场对照(只看预测或实际结果概率在三路间有变化的场) ——
    print("\n" + "=" * 96)
    print("逐场三路对照(实际结果概率 p_actual: A基线→B含金量→C完整):")
    print("=" * 96)
    cmp = pd.DataFrame({
        "match": rA["match"], "real": rA["real"], "actual": rA["actual"],
        "pA_A": rA.apply(lambda x: x["pH"] if x["actual"] == "H"
                         else (x["pD"] if x["actual"] == "D" else x["pA"]), axis=1),
        "pA_B": rB.apply(lambda x: x["pH"] if x["actual"] == "H"
                         else (x["pD"] if x["actual"] == "D" else x["pA"]), axis=1),
        "pA_C": rC.apply(lambda x: x["pH"] if x["actual"] == "H"
                         else (x["pD"] if x["actual"] == "D" else x["pA"]), axis=1),
        "hit_A": rA["hit"], "hit_B": rB["hit"], "hit_C": rC["hit"],
    })
    print(cmp.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("-" * 96)

    # —— 机制贡献诊断: 哪些场被改对了/改错了, 归因到含金量还是收缩 ——
    print("\n机制归因(预测对错变化):")
    fixed_by_imp = cmp[(cmp["hit_A"] == "✗") & (cmp["hit_B"] == "✓")]
    fixed_by_shrink = cmp[(cmp["hit_B"] == "✗") & (cmp["hit_C"] == "✓")]
    broken_by_imp = cmp[(cmp["hit_A"] == "✓") & (cmp["hit_B"] == "✗")]
    broken_by_shrink = cmp[(cmp["hit_B"] == "✓") & (cmp["hit_C"] == "✗")]
    print(f"  含金量改对: +{len(fixed_by_imp)} 场  | 含金量改错: -{len(broken_by_imp)} 场")
    print(f"  收缩  改对: +{len(fixed_by_shrink)} 场  | 收缩  改错: -{len(broken_by_shrink)} 场")
    for tag, sub in [("含金量改对", fixed_by_imp), ("含金量改错", broken_by_imp),
                     ("收缩改对", fixed_by_shrink), ("收缩改错", broken_by_shrink)]:
        if len(sub):
            print(f"    [{tag}] " + ", ".join(sub["match"].tolist()))
    print("=" * 96)


if __name__ == "__main__":
    main()

"""
P0-7 回测脚本: 2018+2022 世界杯逐场赛前预测 vs 实际
================================================================
方法: 严格 walk-forward(每场只用 date < 该场的比赛, 防泄露). 对每场同时算 3 个变体:
  A. elo   = DC + 收缩 κ→∞(κ=1e4): 实力纯 Elo 先验, 无 per-team 进球数据(纯 Elo 基准)
  B. dc    = DC 生产版(κ=0): 含金量加权 + 纯 MLE attack/defense(当前生产模型)
  C. dcs   = DC + 收缩 κ=5: Elo 先验 safety-net(P0-5 留的"开/关"对照)

【效率】收缩是 post-hoc → 一场只拟合 1 次 MLE, 用 shrunk_variant 派生 3 个 κ(不重拟合).
DC 用 10 年窗口拟合(实测: 预测与全量差 0.01pp, 拟合 7s vs 35s). Elo 单次顺序遍历取赛前快照.
【防泄露】Elo 按 date 分组, 同日不互用(对齐 DC 的 date<as_of); DC 用 fit_at(严格 <).
【产出】data/processed/backtest_2018_2022.parquet: 逐场 3 变体三概率 + Elo + 命中 + Brier,
       供 P0-8 算 Brier/校准曲线/收缩开/关判决.

运行: .venv/bin/python backend/models/backtest.py
跑测试: .venv/bin/python -m unittest backend.models.test_backtest
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dixon_coles import DixonColes  # noqa: E402
from elo import (  # noqa: E402
    EloModel, INITIAL_RATING, k_factor, goal_multiplier, expected, result_value, home_advantage_for,
)
from match_predictor import wc_neutral_host, WC2026_HOSTS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data" / "processed" / "international_history.parquet"
OUT = ROOT / "data" / "processed" / "backtest_2018_2022.parquet"
OUT_SWEEP = ROOT / "data" / "processed" / "backtest_kappa_sweep.parquet"
OUT_HALF_LIFE = ROOT / "data" / "processed" / "backtest_half_life_sweep.parquet"

WINDOW_DAYS = 365 * 10           # DC 拟合窗口(10 年: 预测与全量差 0.01pp, 拟合快 5×)
KAPPAS = {"elo": 1e4, "dc": 0.0, "dcs": 5.0}   # 变体 → κ(A 纯Elo / B 生产 / C 收缩)
SWEEP_KAPPAS = {"k0": 0.0, "k3": 3.0, "k5": 5.0, "k10": 10.0, "k20": 20.0,
                "k50": 50.0, "k100": 100.0, "k500": 500.0, "k1e4": 1e4}  # P0-8 κ 细扫
HALF_LIFE_SWEEP = {0: 0.0, 730: 730.0, 1095: 1095.0, 1460: 1460.0, 2190: 2190.0}  # Elo 半衰期细扫(天): 0=基线 / 2,3,4,6 年

# 2018/2022 东道主(本土场享基础 γ; 与 match_predictor.WC2026_HOSTS 分开, 历史东道主不同)
HOSTS_BY_YEAR = {
    2018: {"Russia"},
    2022: {"Qatar"},
}


# ============================================================
# Elo walk-forward: 单次顺序遍历, 按 date 分组取"赛前(date<该日)"快照
# ============================================================
def walk_forward_elo(df: pd.DataFrame, bt_mask: pd.Series, half_life: float = 0.0) -> dict:
    """各回测场赛前(date < 该日)Elo 快照.

    用 EloModel.ratings_at(df, d, half_life) per-call: 基准=该场日期(防泄露, 对齐 DC date<as_of);
    half_life>0 时启用时间衰减(近期比赛权重更高, 修 Elo 滞后, 见 memory elo-recency-lag-brazil).
    同日回测场共享快照(同日不互相喂). half_life=0 数值 == 历史顺序遍历版(红线 test_snapshot_matches_ratings_at).
    返回 {df_row_index -> {team: rating}}.
    """
    snapshots: dict = {}
    cache: dict = {}   # (date, half_life) → ratings, 同日回测场共享
    for idx in df.index[bt_mask]:
        d = df.loc[idx, "date"]
        key = (d, half_life)
        if key not in cache:
            cache[key] = EloModel().ratings_at(df, d, half_life=half_life)
        snapshots[idx] = cache[key]
    return snapshots


# ============================================================
# DC walk-forward: 每个回测场 1 次窗口 MLE 拟合 → 派生 3 个 κ 变体
# ============================================================
def predict_backtest_match(df: pd.DataFrame, match: pd.Series, elo_snap: dict,
                           kappas: dict = KAPPAS, return_mle: bool = False):
    """对单场回测赛: 10 年窗口 fit MLE → shrunk_variant 派生各 κ 变体 → 各自 predict.

    kappas: {变体名: κ}. 默认 KAPPAS(A 纯Elo/B 生产/C 收缩). return_mle=True 额外返回 MLE
    模型(供 κ 细扫算 style retention). 返回该场预测字段 dict(+ 可选 m_mle).
    """
    d = pd.Timestamp(match["date"])
    win = df[(df["date"] >= d - pd.Timedelta(days=WINDOW_DAYS)) & (df["date"] < d)]
    m_mle = DixonColes().fit_at(win, d)          # 1 次 MLE(κ=0, 含金量加权)

    year = int(match["year"])
    hosts = HOSTS_BY_YEAR.get(year, set())
    neutral, host_home, host_away = wc_neutral_host(match["home_team"], match["away_team"], hosts)

    out = {
        "neutral": neutral, "host_home": host_home, "host_away": host_away,
        "elo_home": elo_snap.get(match["home_team"], INITIAL_RATING),
        "elo_away": elo_snap.get(match["away_team"], INITIAL_RATING),
    }
    # 原始 Elo 期望分(home 视角, 含主场 H) —— P0-8 最简 Elo 基准参考
    H = home_advantage_for(match["city"], bool(match["neutral"]))
    out["elo_exp"] = expected(out["elo_home"], out["elo_away"], H)

    for vname, kappa in kappas.items():
        m_var = m_mle if kappa == 0.0 else m_mle.shrunk_variant(elo_snap, kappa)
        p = m_var.predict(match["home_team"], match["away_team"],
                          neutral=neutral, host_home=host_home, host_away=host_away)
        out[f"{vname}_home_win"] = p["home_win"]
        out[f"{vname}_draw"] = p["draw"]
        out[f"{vname}_away_win"] = p["away_win"]
    if return_mle:
        return out, m_mle
    return out


def brier_multiclass(pH, pD, pA, actual):
    oH, oD, oA = (1.0 if actual == k else 0.0 for k in "HDA")
    return (pH - oH) ** 2 + (pD - oD) ** 2 + (pA - oA) ** 2


def outcome(h, a):
    return "H" if h > a else ("D" if h == a else "A")


# ============================================================
# 主流程
# ============================================================
def run_backtest(hist_path=HIST, out_path=OUT, kappas=KAPPAS, verbose=True):
    df = pd.read_parquet(hist_path)
    bt = df[(df["tournament"] == "FIFA World Cup") & (df["year"].isin([2018, 2022]))].sort_values("date")
    bt_mask = df.index.isin(bt.index)
    bt_mask_series = pd.Series(bt_mask, index=df.index)

    if verbose:
        print(f"回测: {len(bt)} 场(2018+2022 WC). walk-forward(严格 date<该场, 防泄露).")
        print(f"DC 窗口 {WINDOW_DAYS // 365} 年, 单 MLE 派生 3 变体 κ={KAPPAS}. 拟合中…")
    t0 = time.time()
    elo_snaps = walk_forward_elo(df, bt_mask_series)
    if verbose:
        print(f"  Elo walk-forward 完成({len(elo_snaps)} 快照), {time.time()-t0:.1f}s")

    rows = []
    for i, (idx, match) in enumerate(bt.iterrows()):
        pred = predict_backtest_match(df, match, elo_snaps[idx], kappas=kappas)
        actual = outcome(int(match["home_score"]), int(match["away_score"]))
        row = {
            "date": match["date"], "year": int(match["year"]),
            "home_team": match["home_team"], "away_team": match["away_team"],
            "home_score": int(match["home_score"]), "away_score": int(match["away_score"]),
            "actual": actual,
            **pred,
        }
        for vname in kappas:
            probs = {"H": pred[f"{vname}_home_win"], "D": pred[f"{vname}_draw"], "A": pred[f"{vname}_away_win"]}
            top = max(probs, key=probs.get)
            row[f"{vname}_pred"] = top
            row[f"{vname}_hit"] = top == actual
            row[f"{vname}_brier"] = brier_multiclass(probs["H"], probs["D"], probs["A"], actual)
        rows.append(row)
        if verbose and (i + 1) % 16 == 0:
            print(f"  {i+1}/{len(bt)} 场, {time.time()-t0:.0f}s")

    res = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res.to_parquet(out_path, index=False)
    if verbose:
        print(f"\n已存 {out_path.name} ({len(res)} 场), 总耗时 {time.time()-t0:.0f}s")
        _print_summary(res)
    return res


def _print_summary(res):
    print("\n" + "=" * 78)
    print(f"{'变体':<10}{'准确率':>9}{'正确':>8}{'平均Brier':>12}{'预测平局占比':>14}")
    print("-" * 78)
    for vname, label in [("elo", "A.纯Elo"), ("dc", "B.DC生产"), ("dcs", "C.DC+收缩")]:
        acc = res[f"{vname}_hit"].mean()
        bri = res[f"{vname}_brier"].mean()
        draw_pct = (res[f"{vname}_pred"] == "D").mean()
        print(f"{label:<10}{acc:>8.1%}{int(res[f'{vname}_hit'].sum()):>4}/{len(res):<3}"
              f"{bri:>12.4f}{draw_pct:>13.1%}")
    print("-" * 78)
    print("(随机基线 准确率33% / Brier≈0.667)")
    # 收缩强度趋势(A>C>B 单调? 即"越收缩越好"?)
    accs = {v: res[f"{v}_hit"].mean() for v in ["elo", "dc", "dcs"]}
    bris = {v: res[f"{v}_brier"].mean() for v in ["elo", "dc", "dcs"]}
    print(f"\n收缩强度趋势 A(κ→∞) > C(κ=5) > B(κ=0)?")
    print(f"  准确率: 纯Elo {accs['elo']:.1%} > 收缩 {accs['dcs']:.1%} > 生产 {accs['dc']:.1%}"
          f"  → {'是, 单调(越收缩越好)' if accs['elo']>=accs['dcs']>=accs['dc'] else '否'}")
    print(f"  Brier : 纯Elo {bris['elo']:.4f} < 收缩 {bris['dcs']:.4f} < 生产 {bris['dc']:.4f}"
          f"  → {'是, 单调' if bris['elo']<=bris['dcs']<=bris['dc'] else '否'}")
    # 收缩开/关判决(P0-5 留的债: B vs C)
    print(f"\n收缩开/关对照(B 生产 vs C 收缩): 准确率 {accs['dc']:.1%}→{accs['dcs']:.1%} | "
          f"Brier {bris['dc']:.4f}→{bris['dcs']:.4f}", end="")
    if bris['dcs'] < bris['dc'] and accs['dcs'] >= accs['dc']:
        verdict = "收缩略优"
        print(f"  → {verdict}")
        print("⚠️ 128 场证据倾向开收缩(且 A>C>B 单调) → 建议 P0-9 复议: 收缩默认启用 / 甚至加大")
    elif abs(bris['dc'] - bris['dcs']) < 0.002:
        print("  → 无差别")
        print("→ 128 场仍支持当前“生产默认不收缩”决策(选项2). 校准曲线定夺留 P0-8.")
    else:
        print("  → 不收缩略优")
        print("→ 128 场仍支持当前“生产默认不收缩”决策(选项2).")
    # 平局系统性(历史已知: DC 低估平局)
    actual_draw = (res["actual"] == "D").mean()
    print(f"\n实际平局率 {actual_draw:.1%}; 各变体预测平局占比见上表(低=系统性低估平局)")
    print(f"注: 纯Elo 预测 0% 平局 —— 单强度先验抹平攻防风格, 偶数对阵平局概率高但很少成 top")
    print("=" * 78)


def run_kappa_sweep(hist_path=HIST, out_path=OUT_SWEEP, kappas=SWEEP_KAPPAS, verbose=True):
    """P0-8 κ 细扫: 同 walk-forward(单 MLE 派生多 κ), 输出 准确率/Brier/风格保留 vs κ.

    风格保留 = 各队 log(net_strength) 的 std(越大=攻防风格差异越保留). κ→∞ 时 net→1, std→0.
    产出 backtest_kappa_sweep.parquet(每 κ 一行) → P0-9 选生产 κ 的决策输入.
    """
    df = pd.read_parquet(hist_path)
    bt = df[(df["tournament"] == "FIFA World Cup") & (df["year"].isin([2018, 2022]))].sort_values("date")
    bt_mask = pd.Series(df.index.isin(bt.index), index=df.index)
    if verbose:
        print(f"κ 细扫: {len(bt)} 场, κ 档 {list(kappas.values())}. 拟合中(同 walk-forward, ~15min)…")
    t0 = time.time()
    elo_snaps = walk_forward_elo(df, bt_mask)
    hits = {k: 0 for k in kappas}
    briers = {k: 0.0 for k in kappas}
    style = {k: [] for k in kappas}
    n = 0
    for i, (idx, match) in enumerate(bt.iterrows()):
        pred, m_mle = predict_backtest_match(df, match, elo_snaps[idx], kappas=kappas, return_mle=True)
        actual = outcome(int(match["home_score"]), int(match["away_score"]))
        onehot = {"H": 0.0, "D": 0.0, "A": 0.0}
        onehot[actual] = 1.0
        for vname, kappa in kappas.items():
            pH = pred[f"{vname}_home_win"]; pD = pred[f"{vname}_draw"]; pA = pred[f"{vname}_away_win"]
            top = max([("H", pH), ("D", pD), ("A", pA)], key=lambda x: x[1])[0]
            hits[vname] += int(top == actual)
            briers[vname] += (pH - onehot["H"]) ** 2 + (pD - onehot["D"]) ** 2 + (pA - onehot["A"]) ** 2
            m_var = m_mle if kappa == 0.0 else m_mle.shrunk_variant(elo_snaps[idx], kappa)
            nets = np.array([m_var.attack[t] / m_var.defense[t] for t in m_var.teams])
            style[vname].append(float(np.std(np.log(nets))))
        n += 1
        if verbose and (i + 1) % 16 == 0:
            print(f"  {i+1}/{len(bt)} 场, {time.time()-t0:.0f}s")
    rows = [{"variant": v, "kappa": k, "accuracy": hits[v] / n, "brier": briers[v] / n,
             "style_retention": float(np.mean(style[v]))} for v, k in kappas.items()]
    res = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res.to_parquet(out_path, index=False)
    if verbose:
        print(f"\n已存 {out_path.name}, 总耗时 {time.time()-t0:.0f}s")
        _print_sweep(res)
    return res


def _print_sweep(res):
    print("\n" + "=" * 74)
    print("κ 细扫(准确率/Brier vs 风格保留)")
    print("-" * 74)
    print(f"{'κ':>10}{'准确率':>9}{'Brier':>9}{'风格保留(log net std)':>24}")
    for _, r in res.iterrows():
        ks = "→∞" if r["kappa"] >= 1e4 else f"{r['kappa']:.0f}"
        print(f"{ks:>10}{r['accuracy']:>8.1%}{r['brier']:>9.4f}{r['style_retention']:>22.3f}")
    print("-" * 74)
    best = res.loc[res["brier"].idxmin()]
    print(f"Brier 最优: κ={best['kappa']:.1f}  acc={best['accuracy']:.1%}  brier={best['brier']:.4f}")
    print("→ 选生产 κ = 准确率/Brier(偏小κ档) vs 风格保留/比分分布细节(偏大κ档) 的权衡; P0-9 决策.")
    print("=" * 74)


def run_half_life_sweep(hist_path=HIST, out_path=OUT_HALF_LIFE,
                        half_lifes=HALF_LIFE_SWEEP, kappas=KAPPAS, verbose=True):
    """Elo 半衰期细扫: 各 half_life 衰减 Elo 快照 → 同一 DC MLE 派生收缩变体 → 预测.

    效率关键: DC fit_at 每场只 1 次(κ=0 生产变体不用 Elo, 各 half_life 共享同一 MLE),
    衰减只改 elo_snap → shrunk_variant post-hoc 派生(毫秒). 总耗时 ≈ 单次回测 DC fit(~898s).
    产出 backtest_half_life_sweep.parquet(half_life × variant 一行) → go/no-go 决策输入.
    """
    df = pd.read_parquet(hist_path)
    bt = df[(df["tournament"] == "FIFA World Cup") & (df["year"].isin([2018, 2022]))].sort_values("date")
    bt_mask = pd.Series(df.index.isin(bt.index), index=df.index)
    if verbose:
        print(f"Elo 半衰期细扫: {len(bt)} 场, half_life 档(天) {list(half_lifes.values())}. 拟合中…")
    t0 = time.time()
    elo_snaps_by_hl = {hl: walk_forward_elo(df, bt_mask, half_life=hl) for hl in half_lifes}
    if verbose:
        print(f"  Elo 快照({len(half_lifes)} 档, 衰减基准=各场日期)完成, {time.time()-t0:.0f}s")
    hits = {hl: {v: 0 for v in kappas} for hl in half_lifes}
    briers = {hl: {v: 0.0 for v in kappas} for hl in half_lifes}
    n = 0
    for i, (idx, match) in enumerate(bt.iterrows()):
        d = pd.Timestamp(match["date"])
        win = df[(df["date"] >= d - pd.Timedelta(days=WINDOW_DAYS)) & (df["date"] < d)]
        m_mle = DixonColes().fit_at(win, d)        # 1 次 MLE, 各 half_life 共享
        actual = outcome(int(match["home_score"]), int(match["away_score"]))
        onehot = {"H": 0.0, "D": 0.0, "A": 0.0}
        onehot[actual] = 1.0
        year = int(match["year"])
        hosts = HOSTS_BY_YEAR.get(year, set())
        neutral, host_home, host_away = wc_neutral_host(match["home_team"], match["away_team"], hosts)
        for hl in half_lifes:
            elo_snap = elo_snaps_by_hl[hl][idx]
            for vname, kappa in kappas.items():
                m_var = m_mle if kappa == 0.0 else m_mle.shrunk_variant(elo_snap, kappa)
                p = m_var.predict(match["home_team"], match["away_team"],
                                  neutral=neutral, host_home=host_home, host_away=host_away)
                pH, pD, pA = p["home_win"], p["draw"], p["away_win"]
                top = max([("H", pH), ("D", pD), ("A", pA)], key=lambda x: x[1])[0]
                hits[hl][vname] += int(top == actual)
                briers[hl][vname] += (pH - onehot["H"]) ** 2 + (pD - onehot["D"]) ** 2 + (pA - onehot["A"]) ** 2
        n += 1
        if verbose and (i + 1) % 16 == 0:
            print(f"  {i+1}/{len(bt)} 场, {time.time()-t0:.0f}s")
    rows = [{"half_life": hl, "variant": v, "kappa": kappas[v],
             "accuracy": hits[hl][v] / n, "brier": briers[hl][v] / n}
            for hl in half_lifes for v in kappas]
    res = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res.to_parquet(out_path, index=False)
    if verbose:
        print(f"\n已存 {out_path.name}, 总耗时 {time.time()-t0:.0f}s")
        _print_half_life(res)
    return res


def _print_half_life(res):
    print("\n" + "=" * 70)
    print("Elo 半衰期细扫(准确率/Brier vs half_life; 仅收缩变体 dcs/elo 受 Elo 影响)")
    print("-" * 70)
    print(f"{'half_life':>10}{'变体':>8}{'准确率':>9}{'Brier':>9}")
    for _, r in res.iterrows():
        hl = "基线0" if r["half_life"] == 0 else f"{int(r['half_life'])}天"
        print(f"{hl:>10}{r['variant']:>8}{r['accuracy']:>8.1%}{r['brier']:>9.4f}")
    print("-" * 70)
    print("go 标准: 衰减变体 dcs/elo 的 Brier ≤ 基线+0.002 & 准确率 ≥ 基线−1pp")
    print("注: dc(κ=0) 变体不用 Elo → 各 half_life 下应完全相同(数值校验)")
    print("=" * 70)


if __name__ == "__main__":
    if "--summary" in sys.argv:
        # 从已存 parquet 直接出汇总(不重拟合), 便于复看 / P0-8 预热
        res = pd.read_parquet(OUT)
        print(f"读取 {OUT.name} ({len(res)} 场) — 仅汇总, 不重拟合\n")
        _print_summary(res)
    elif "--kappa-sweep" in sys.argv:
        # P0-8: κ 细扫(同 walk-forward, 单 MLE 派生多 κ), 找准确率-vs-风格权衡
        run_kappa_sweep()
    elif "--half-life-sweep" in sys.argv:
        # Elo 时间衰减半衰期细扫(修 Elo 滞后); 单 MLE 共享, 只换衰减 elo_snap
        run_half_life_sweep()
    else:
        run_backtest()

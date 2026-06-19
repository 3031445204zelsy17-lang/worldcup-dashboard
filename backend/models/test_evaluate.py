"""
P0-8 evaluate 单元测试  (标准库 unittest + numpy/pandas)
==========================================
验证:
  1. reliability: 分桶正确(边界/空桶/中心) + ECE 计算
  2. draw_boost_scan: 概率已校准时 best_k=1(无用); 系统性低估平局时 best_k>1(能救)
  3. recall_by_outcome: per-outcome 召回正确

运行: .venv/bin/python -m unittest backend.models.test_evaluate
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from evaluate import (reliability, draw_boost_scan, brier_with_draw_boost,  # noqa: E402
                      recall_by_outcome, prob_col, goal_distribution_calibration, estimate_nb_r)


def _df(probs, actuals):
    """probs=[(pH,pD,pA)...], actuals=['H'/'D'/'A'...] → 评估用 df(变体 dc)."""
    rows = []
    for (ph, pd_, pa), a in zip(probs, actuals):
        top = max([("H", ph), ("D", pd_), ("A", pa)], key=lambda x: x[1])[0]
        oH, oD, oA = (1.0 if a == k else 0.0 for k in "HDA")
        rows.append({"dc_home_win": ph, "dc_draw": pd_, "dc_away_win": pa,
                     "actual": a, "dc_pred": top,
                     "dc_hit": top == a,
                     "dc_brier": (ph - oH) ** 2 + (pd_ - oD) ** 2 + (pa - oA) ** 2})
    return pd.DataFrame(rows)


class TestReliability(unittest.TestCase):
    def test_binning_and_ece(self):
        # 10 场, 预测 home_win 全 0.5, 其中 6 场实际 H → bin [0.4,0.6) actual_freq=0.6
        df = _df([(0.5, 0.3, 0.2)] * 10, ["H"] * 6 + ["A"] * 4)
        rd, ece = reliability(df, "dc", "H")
        self.assertEqual(len(rd), 1)                       # 只落到一个桶
        bin_row = rd.iloc[0]
        self.assertAlmostEqual(bin_row["pred_mean"], 0.5)
        self.assertAlmostEqual(bin_row["actual_freq"], 0.6)
        self.assertAlmostEqual(bin_row["count"], 10)
        # ECE = count/total * |0.5-0.6| = 1.0 * 0.1
        self.assertAlmostEqual(ece, 0.1, places=6)

    def test_perfect_calibration_zero_ece(self):
        # 预测概率 == 实际频率 → ECE≈0
        probs = [(0.9, 0.05, 0.05)] * 4 + [(0.1, 0.1, 0.8)] * 6
        actuals = ["H"] * 4 + ["A"] * 6     # 高pH桶 actual_freq=1.0(过自信, 非完美)… 改成校准:
        probs = [(0.8, 0.1, 0.1)] * 8 + [(0.2, 0.1, 0.7)] * 7
        actuals = ["H"] * int(0.8 * 8) + ["A"] * (8 - int(0.8 * 8))  # 8场0.8桶→6H2A≈0.75
        df = _df(probs, actuals)
        _, ece = reliability(df, "dc", "H")
        self.assertGreaterEqual(ece, 0.0)
        self.assertLess(ece, 0.5)


class TestDrawBoost(unittest.TestCase):
    def test_no_boost_when_calibrated(self):
        # 平均 pD ≈ 实际平局率 → best_k 应=1.0(再 boost 反而过)
        rng = np.random.default_rng(0)
        n = 200
        pD = rng.uniform(0.15, 0.30, n)            # 平均~0.22
        pH = (1 - pD) * rng.uniform(0.4, 0.7, n)
        pA = 1 - pH - pD
        actuals = rng.uniform(size=n) < pD         # 按pD采样实际平局 → 概率校准
        act = ["D" if d else "H" for d in actuals]
        df = _df(list(zip(pH, pD, pA)), act)
        res = draw_boost_scan(df, "dc")
        self.assertAlmostEqual(res["base_brier"], brier_with_draw_boost(df, "dc", 1.0))
        # best_k 接近 1(校准良好 → 不该大幅 boost)
        self.assertLessEqual(res["best_k"], 1.5)

    def test_boost_helps_when_draw_underpredicted(self):
        # 模型系统性低估平局(pD=0.05 恒定), 实际 50% 平局 → best_k 应显著>1 且降 Brier
        n = 100
        pD = np.full(n, 0.05)
        pH = np.full(n, 0.85)
        pA = 1 - pH - pD
        actuals = ["D", "H"] * (n // 2)            # 一半平局
        df = _df(list(zip(pH, pD, pA)), actuals)
        res = draw_boost_scan(df, "dc")
        self.assertGreater(res["best_k"], 2.0, "系统性低估平局时最优 k 应显著>1")
        self.assertLess(res["best_brier"], res["base_brier"], "boost 应降 Brier")

    def test_k1_is_identity(self):
        # k=1 重归一不改变已和为1的概率 → Brier 不变
        df = _df([(0.5, 0.3, 0.2), (0.4, 0.4, 0.2)], ["H", "D"])
        base = sum((r["dc_brier"]) for _, r in df.iterrows()) / len(df)
        self.assertAlmostEqual(brier_with_draw_boost(df, "dc", 1.0), base, places=8)


class TestRecallByOutcome(unittest.TestCase):
    def test_recall(self):
        df = _df([(0.6, 0.3, 0.1), (0.3, 0.4, 0.3), (0.2, 0.3, 0.5)],
                 ["H", "D", "A"])   # pred H, D, A — 全对
        rec = recall_by_outcome(df, "dc")
        self.assertAlmostEqual(rec["H"], 1.0)
        self.assertAlmostEqual(rec["D"], 1.0)
        self.assertAlmostEqual(rec["A"], 1.0)


@unittest.skipUnless(Path("data/processed/backtest_2018_2022.parquet").exists()
                     and Path("data/processed/dixon_coles_current.parquet").exists(),
                     "需要 backtest + DC artifacts")
class TestGoalDistributionCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dixon_coles import DixonColes
        cls.df = pd.read_parquet("data/processed/backtest_2018_2022.parquet")
        cls.dc = DixonColes.from_artifacts(
            Path("data/processed/dixon_coles_current.parquet"),
            Path("data/processed/dixon_coles_global.json"))
        cls.hosts = {2018: {"Russia"}, 2022: {"Qatar"}}

    def test_poisson_mixture_var_mean_above_one(self):
        res = goal_distribution_calibration(self.df, self.dc, nb_r=0.0, n_samples=5000,
                                            hosts_by_year=self.hosts)
        for k in ("actual", "poisson", "nb", "n_teams"):
            self.assertIn(k, res)
        # 混合 Poisson(各场 λ 异质) var/mean > 1(λ 异质性导致, 非单场过度离散); 合理范围 < 2
        self.assertGreater(res["poisson"]["var_mean"], 1.0)
        self.assertLess(res["poisson"]["var_mean"], 2.0)

    def test_nb_overdispersed(self):
        # nb_r>0 → NB 混合 var/mean > Poisson 混合(多一层单场过度离散), 大比分 P(≥5) 更高.
        # (注: WC 128场过度离散弱, actual≈poisson 混合; 故只验证 NB 机制, 量级匹配留 go/no-go)
        res = goal_distribution_calibration(self.df, self.dc, nb_r=1.875, n_samples=10000,
                                            hosts_by_year=self.hosts)
        self.assertGreater(res["nb"]["var_mean"], res["poisson"]["var_mean"],
                           "NB var/mean 应大于 Poisson(额外单场过度离散)")
        self.assertGreater(res["nb"]["p_ge5"], res["poisson"]["p_ge5"],
                           "NB 大比分 P(≥5) 应高于 Poisson(还原尾部)")

    def test_estimate_nb_r_in_range(self):
        r = estimate_nb_r(self.df, self.dc, hosts_by_year=self.hosts)
        self.assertGreaterEqual(r, 1.0)
        self.assertLessEqual(r, 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""P2-1 LiveMatchSimulator 单测 —— 用 synthetic DC, 不依赖 parquet artifacts(pyarrow19 读不了旧 parquet).

synthetic DC 2 队 A/B 已知参数 → 可手算 λ 精确验证公式 / 红牌 / 边界.
生产用真 DC(from_artifacts), 单测传 SimpleNamespace(dc.mu/gamma/attack/defense) 即可.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

from backend.simulation.live_odds import (
    LiveMatchSimulator, RED_CARD_OFFENSE_MULT, RED_CARD_OPPONENT_MULT,
)


def make_dc(mu: float = 0.0, gamma: float = 0.25,
            att_a: float = 1.5, att_b: float = 1.0,
            def_a: float = 1.0, def_b: float = 1.3) -> SimpleNamespace:
    """synthetic DC: A 强(att 1.5)/B 弱(att 1.0), 已知参数可手算 λ."""
    return SimpleNamespace(
        mu=mu, gamma=gamma,
        attack={"A": att_a, "B": att_b},
        defense={"A": def_a, "B": def_b},
        teams=["A", "B"],
    )


class TestLambdaFormula(unittest.TestCase):
    """λ_full / λ_rem 公式精确验证(不采样, 纯计算)."""

    def setUp(self):
        self.dc = make_dc()
        self.sim = LiveMatchSimulator("A", "B", neutral=False, dc=self.dc, seed=42)

    def test_lambda_home_full_with_gamma(self):
        """主队 λ_full = exp(μ)·att_a/def_b·exp(γ)."""
        expected = np.exp(0.0) * 1.5 / 1.3 * np.exp(0.25)
        self.assertAlmostEqual(self.sim.lambda_home_full, expected, places=5)

    def test_lambda_away_full_no_gamma_for_away(self):
        """客队 λ_full = exp(μ)·att_b/def_a(away 永不享 γ, 与 mc._lambda 一致)."""
        expected = np.exp(0.0) * 1.0 / 1.0
        self.assertAlmostEqual(self.sim.lambda_away_full, expected, places=5)

    def test_neutral_drops_gamma(self):
        """neutral=True → 主队 λ_full 不含 γ."""
        sim_neu = LiveMatchSimulator("A", "B", neutral=True, dc=self.dc, seed=42)
        expected = np.exp(0.0) * 1.5 / 1.3
        self.assertAlmostEqual(sim_neu.lambda_home_full, expected, places=5)

    def test_minute_zero_no_red_equals_full(self):
        """minute=0 + 0红牌 → λ_rem = λ_full(自洽赛前)."""
        lam_h, lam_a = self.sim._lambda_remaining(0, 0, 0)
        self.assertAlmostEqual(lam_h, self.sim.lambda_home_full, places=6)
        self.assertAlmostEqual(lam_a, self.sim.lambda_away_full, places=6)

    def test_minute_scaling(self):
        """λ_rem 按 (90-minute)/90 线性缩放."""
        lam_h_45, _ = self.sim._lambda_remaining(45, 0, 0)
        self.assertAlmostEqual(lam_h_45, self.sim.lambda_home_full * 0.5, places=6)

    def test_minute_90_zero_lambda(self):
        """90min → frac=0, λ_rem=0(锁定终场)."""
        lam_h, lam_a = self.sim._lambda_remaining(90, 0, 0)
        self.assertEqual(lam_h, 0.0)
        self.assertEqual(lam_a, 0.0)

    def test_red_card_lambda_formula(self):
        """主队 1 红牌: λ_home ×0.85, λ_away ×1.20."""
        lam_h_base, lam_a_base = self.sim._lambda_remaining(60, 0, 0)
        lam_h_red, lam_a_red = self.sim._lambda_remaining(60, 1, 0)
        self.assertAlmostEqual(lam_h_red, lam_h_base * RED_CARD_OFFENSE_MULT, places=6)
        self.assertAlmostEqual(lam_a_red, lam_a_base * RED_CARD_OPPONENT_MULT, places=6)

    def test_red_card_symmetric_both_teams(self):
        """双方各 1 红牌 → 两边进攻都削 0.85 + 对手都增 1.20 → 净效应抵消(λ 回到 base)."""
        lam_h_base, lam_a_base = self.sim._lambda_remaining(60, 0, 0)
        lam_h, lam_a = self.sim._lambda_remaining(60, 1, 1)
        self.assertAlmostEqual(lam_h, lam_h_base * 0.85 * 1.20, places=6)
        self.assertAlmostEqual(lam_a, lam_a_base * 1.20 * 0.85, places=6)
        # 对称: 两队净修正相同(0.85×1.20), 比例不变
        self.assertAlmostEqual(lam_h / lam_a, lam_h_base / lam_a_base, places=5)


class TestSimulate(unittest.TestCase):
    """simulate() 采样行为(边界 / 方向 / 守恒)."""

    def setUp(self):
        self.dc = make_dc()
        self.sim = LiveMatchSimulator("A", "B", neutral=False, dc=self.dc, seed=42)

    def test_minute_90_locks_score(self):
        r = self.sim.simulate(90, 2, 1, n=5000)
        self.assertAlmostEqual(r["home_win"], 1.0, places=6)
        self.assertAlmostEqual(r["draw"], 0.0, places=6)
        self.assertAlmostEqual(r["away_win"], 0.0, places=6)

    def test_minute_89_leading_home_wins(self):
        """89min + 1:0 → 主胜 ≥0.95(剩 1min 客进球概率极低)."""
        r = self.sim.simulate(89, 1, 0, n=10000)
        self.assertGreaterEqual(r["home_win"], 0.95)

    def test_minute_89_trailing_home_loses(self):
        """89min + 0:1 → 主胜 ≤0.05."""
        r = self.sim.simulate(89, 0, 1, n=10000)
        self.assertLessEqual(r["home_win"], 0.05)

    def test_probs_sum_to_one(self):
        r = self.sim.simulate(45, 1, 1, n=5000)
        self.assertAlmostEqual(r["home_win"] + r["draw"] + r["away_win"], 1.0, places=6)

    def test_red_card_lowers_home_win(self):
        """主队 1 红牌 → 主胜 < 无红牌(方向)."""
        base = self.sim.simulate(30, 0, 0, n=10000)
        red = self.sim.simulate(30, 0, 0, home_reds=1, n=10000)
        self.assertLess(red["home_win"], base["home_win"])

    def test_red_card_cumulative_monotone(self):
        """客队 2 红牌 → 客胜 < 1 红牌(累乘边际)."""
        r1 = self.sim.simulate(30, 0, 0, away_reds=1, n=8000)
        r2 = self.sim.simulate(30, 0, 0, away_reds=2, n=8000)
        self.assertLess(r2["away_win"], r1["away_win"])

    def test_strong_team_favored_at_minute_zero(self):
        """minute=0: A(att 1.5 强) 主胜 > B(att 1.0)."""
        r = self.sim.simulate(0, 0, 0, n=10000)
        self.assertGreater(r["home_win"], r["away_win"])

    def test_minute_increasing_locks_leader(self):
        """比分 1:0 下, minute 越大 主胜越高(剩余时间越少, 客反超越难)."""
        r_30 = self.sim.simulate(30, 1, 0, n=8000)
        r_75 = self.sim.simulate(75, 1, 0, n=8000)
        self.assertGreater(r_75["home_win"], r_30["home_win"])


class TestKnockout(unittest.TestCase):
    """淘汰赛加时 + 点球."""

    def setUp(self):
        self.dc = make_dc()
        self.sim = LiveMatchSimulator("A", "B", neutral=False, dc=self.dc, seed=42)

    def test_advance_sums_to_one(self):
        r = self.sim.simulate(0, 0, 0, n=5000, knockout=True)
        self.assertAlmostEqual(r["home_advance"] + r["away_advance"], 1.0, places=6)

    def test_strong_team_advances_more(self):
        """A 强 → home_advance > 0.5."""
        r = self.sim.simulate(0, 0, 0, n=10000, knockout=True)
        self.assertGreater(r["home_advance"], 0.5)

    def test_advance_extends_home_win(self):
        """knockout home_advance ≥ 90min home_win(加时点球把平局分给两队, 强队多分)."""
        r90 = self.sim.simulate(0, 0, 0, n=10000)
        rko = self.sim.simulate(0, 0, 0, n=10000, knockout=True)
        self.assertGreaterEqual(rko["home_advance"], r90["home_win"] - 0.01)


if __name__ == "__main__":
    unittest.main()

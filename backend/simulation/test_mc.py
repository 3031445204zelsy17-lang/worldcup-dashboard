"""
P1-3 mc.py 单测(Monte Carlo 模拟器: 确定性/守恒/单调/锁定/集成).
跑: .venv/bin/python -m unittest backend.simulation.test_mc
依赖: data/raw/results.csv(默认赛程)+ DC artifacts. 无则 skip.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.simulation.mc import MonteCarloSimulator  # noqa: E402

LOCAL = ROOT / "data" / "raw" / "results.csv"


@unittest.skipUnless(LOCAL.exists(), "无本地 results.csv 副本, 跳过")
class TestMonteCarlo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = MonteCarloSimulator(seed=42)
        cls.probs = cls.sim.run(n=1000)

    def test_deterministic_same_seed(self):
        # 同 seed 两个独立 sim → 结果完全相同
        p1 = MonteCarloSimulator(seed=42).run(n=100)
        p2 = MonteCarloSimulator(seed=42).run(n=100)
        self.assertEqual(p1, p2)

    def test_win_prob_sums_to_one(self):
        win = {t: p["win"] for t, p in self.probs.items()}
        self.assertAlmostEqual(sum(win.values()), 1.0, places=2)

    def test_48_teams(self):
        self.assertEqual(len(self.probs), 48)

    def test_group_advancement_is_one(self):
        for t, p in self.probs.items():
            self.assertEqual(p["group"], 1.0)

    def test_monotone_advancement(self):
        # ro32 ≥ ro16 ≥ qf ≥ sf ≥ final ≥ win(累积晋级)
        for t, p in self.probs.items():
            seq = [p["ro32"], p["ro16"], p["qf"], p["sf"], p["final"], p["win"]]
            self.assertEqual(seq, sorted(seq, reverse=True), f"{t} 非单调: {seq}")

    def test_finished_fixtures_loaded(self):
        # 默认赛程(6/11-6/14)有 12 场已完赛 → _gfin mask 标记, 实际比分读入
        sim = MonteCarloSimulator(seed=42)
        self.assertEqual(int(sim._gfin.sum()), 12)
        # finished 场的实际比分应非默认 0(至少有一场进球)
        self.assertGreater(int(sim._gacth[sim._gfin].sum()), 0)

    def test_lambda_home_advantage(self):
        # 中立场 neutral=True → home 无 γ; 非中立场 home λ 更高
        import numpy as np
        sim = MonteCarloSimulator(seed=42)
        # 取两队, 中立 vs 非中立
        hi = np.array([sim.team_idx["Spain"]])
        ai = np.array([sim.team_idx["Japan"]])
        lam_h_neu, _ = sim._lambda(hi, ai, np.array([True]))
        lam_h_home, _ = sim._lambda(hi, ai, np.array([False]))
        self.assertGreater(lam_h_home[0], lam_h_neu[0])

    def test_top_teams_reasonable(self):
        # Elo 前 2(Spain/Argentina)应在夺冠 Top3
        win = {t: p["win"] for t, p in self.probs.items()}
        top3 = {t for t, _ in sorted(win.items(), key=lambda x: -x[1])[:3]}
        self.assertTrue({"Spain", "Argentina"} & top3, f"Spain/Argentina 不在 Top3: {top3}")
        # 无队夺冠概率超 30%(单队 WC 天花板)
        self.assertLess(max(win.values()), 0.30)

    def test_finished_scores_locked(self):
        # 已完赛场比分必须锁定为实际值(不重新采样)—— P1-2 worker 重算语义的核心
        sim = MonteCarloSimulator(seed=42)
        *_, hg, ag = sim._simulate_group_stage(50, return_scores=True)
        fin = sim._gfin
        for i in range(50):
            self.assertTrue((hg[i, fin] == sim._gacth[fin]).all(), "finished home 比分未锁定")
            self.assertTrue((ag[i, fin] == sim._gacta[fin]).all(), "finished away 比分未锁定")

    def test_extra_time_lambda_fraction_sane(self):
        # 加时 λ 衰减常量: 30min/90min = 1/3, 在 (0,1)
        from backend.simulation.mc import EXTRA_TIME_LAMBDA_FRACTION
        self.assertGreater(EXTRA_TIME_LAMBDA_FRACTION, 0)
        self.assertLess(EXTRA_TIME_LAMBDA_FRACTION, 1)
        self.assertAlmostEqual(EXTRA_TIME_LAMBDA_FRACTION, 1 / 3, places=2)

    def test_knockout_non_draw_returns_winner(self):
        # 常规时间分出胜负 → 直接返回胜者(不进加时)
        import numpy as np
        sim = MonteCarloSimulator(seed=42)
        spain = sim.team_idx["Spain"]; canada = sim.team_idx["Canada"]
        h = np.array([spain]); a = np.array([canada]); neu = np.array([True])
        self.assertEqual(sim._decide_knockout_winners(h, a, np.array([2]), np.array([1]), neu)[0], spain)
        self.assertEqual(sim._decide_knockout_winners(h, a, np.array([0]), np.array([3]), neu)[0], canada)

    def test_knockout_draw_favors_strong_team(self):
        # 90min 平局 → 30min 加时(实力仍有效): 强队 Spain 加时胜率应显著 > 50%.
        # 旧版平局直接翻硬币 → 会 ≈50%; 加时版让强队在加时继续占优. 这是本次修正的核心验证.
        import numpy as np
        sim = MonteCarloSimulator(seed=7)
        spain = sim.team_idx["Spain"]; canada = sim.team_idx["Canada"]   # Elo 2205 vs 1875
        h = np.array([spain]); a = np.array([canada]); neu = np.array([True])
        wins = sum(1 for _ in range(3000)
                   if sim._decide_knockout_winners(h, a, np.array([1]), np.array([1]), neu)[0] == spain)
        rate = wins / 3000
        self.assertGreater(rate, 0.60,
                           f"Spain 平局后加时胜率仅 {rate:.1%}, 未显著 > 50% → 平局处理可能仍是纯翻硬币")


if __name__ == "__main__":
    unittest.main()

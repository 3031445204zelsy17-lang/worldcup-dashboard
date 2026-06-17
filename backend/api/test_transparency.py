"""
P1-6 透明度层测试
================
wilson 置信区间(纯函数) + advancement_path 带 CI + drivers 暴露 DC 攻防参数 +
backtest_summary 平局盲点/分年. drivers 用临时 DB(seed 48 队)+ 真实 DC artifacts.

跑: .venv/bin/python -m unittest backend.api.test_transparency
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402

from backend.api import queries as q  # noqa: E402
from backend.data.schema import Match, Status, init_db, seed_teams  # noqa: E402


# ============================================================
# Wilson 置信区间(纯函数, 边界稳健性)
# ============================================================
class TestWilsonInterval(unittest.TestCase):
    def test_zero_one_bounds_clamped(self):
        lo0, hi0 = q.wilson_interval(0.0)
        self.assertEqual(lo0, 0.0)              # p=0 下界恰 0, 不为负
        self.assertLess(hi0, 0.001)
        lo1, hi1 = q.wilson_interval(1.0)
        self.assertGreater(lo1, 0.999)
        self.assertEqual(hi1, 1.0)              # p=1 上界恰 1, 不超

    def test_contains_point_estimate(self):
        for p in (0.01, 0.146, 0.5, 0.8, 0.99):
            lo, hi = q.wilson_interval(p)
            self.assertLessEqual(lo, p)
            self.assertGreaterEqual(hi, p)

    def test_symmetric_complement(self):
        # Wilson 性质: p 与 1-p 的区间等宽
        for p in (0.1, 0.2, 0.35):
            w = q.wilson_interval(p)[1] - q.wilson_interval(p)[0]
            wc = q.wilson_interval(1 - p)[1] - q.wilson_interval(1 - p)[0]
            self.assertAlmostEqual(w, wc, places=6)

    def test_larger_n_tighter(self):
        w_small = q.wilson_interval(0.2, n=100)[1] - q.wilson_interval(0.2, n=100)[0]
        w_big = q.wilson_interval(0.2, n=100000)[1] - q.wilson_interval(0.2, n=100000)[0]
        self.assertGreater(w_small, w_big)

    def test_n_zero_full_range(self):
        self.assertEqual(q.wilson_interval(0.3, n=0), (0.0, 1.0))

    def test_within_unit_interval(self):
        for p in (-0.5, 0.0, 0.5, 1.0, 1.5):     # clamp 后仍在 [0,1]
            lo, hi = q.wilson_interval(p)
            self.assertGreaterEqual(lo, 0.0)
            self.assertLessEqual(hi, 1.0)


# ============================================================
# advancement_path 每格带 CI
# ============================================================
class TestAdvancementPathCI(unittest.TestCase):
    def _probs(self):
        return {"advancement": {"group": 1.0, "ro32": 0.8, "ro16": 0.5,
                                "qf": 0.3, "sf": 0.15, "final": 0.08},
                "win_prob": 0.05}

    def test_seven_steps_each_with_ci(self):
        path = q.advancement_path(self._probs())
        self.assertEqual(len(path), 7)
        for step in path:
            self.assertIn("ci_low", step)
            self.assertIn("ci_high", step)
            self.assertLessEqual(step["ci_low"], step["prob"])
            self.assertGreaterEqual(step["ci_high"], step["prob"])

    def test_win_step_last(self):
        path = q.advancement_path(self._probs())
        self.assertEqual(path[-1]["round"], "win")
        self.assertAlmostEqual(path[-1]["prob"], 0.05)


# ============================================================
# drivers 暴露 DC 攻防参数(临时 DB seed 48 队 + 真实 artifacts)
# ============================================================
class TestDriversDCParams(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.dbpath = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(cls.dbpath)
        cls.conn = init_db(cls.dbpath, all_tables=True)
        seed_teams(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        Path(cls.dbpath).unlink(missing_ok=True)

    def setUp(self):
        if q.load_dc() is None:
            self.skipTest("DC artifacts 未加载(跳过攻防参数测试)")

    def test_dc_ranks_all_48_in_range(self):
        ranks = q._dc_ranks(self.conn)
        self.assertEqual(len(ranks), 48)
        for r in ranks.values():
            self.assertTrue(1 <= r["attack_rank"] <= 48)
            self.assertTrue(1 <= r["defense_rank"] <= 48)

    def test_team_drivers_attack_defense(self):
        d = q.team_drivers(self.conn, "Spain")
        self.assertIsNotNone(d["attack"])
        self.assertIsNotNone(d["defense"])
        self.assertEqual(d["attack_rank"], 1)          # Spain 攻击最强(生产 κ=20 artifacts)
        self.assertIsNotNone(d["elo_gap_vs_avg"])

    def test_team_drivers_unknown_elo_null_safe(self):
        # teams 表无该队 → 走 elo NULL 分支(不崩)
        d = q.team_drivers(self.conn, "NoSuchTeam")
        self.assertEqual(d["data_status"], "pending")

    def test_match_drivers_params_and_lambda_inputs(self):
        m = Match(date="2026-06-17", home="Spain", away="Japan",
                  home_score=None, away_score=None, status=Status.UPCOMING,
                  neutral=True, source="martj42", kickoff="2026-06-17T19:00:00+00:00")
        d = q.match_drivers(self.conn, m)
        self.assertIsNotNone(d["home_attack"])
        self.assertEqual(d["home_attack_rank"], 1)
        self.assertIsNotNone(d["global_mu"])          # λ=exp(μ)·... 的基础进球率
        self.assertIsNotNone(d["global_gamma"])
        self.assertFalse(d["host_advantage"])         # 西/日均非东道主
        # altitude/weather 留 P2
        self.assertIsNone(d["altitude"])
        self.assertIsNone(d["weather"])


# ============================================================
# backtest_summary 平局盲点 + 分年
# ============================================================
class TestBacktestLimitations(unittest.TestCase):
    def test_has_limitations_and_per_year(self):
        bs = q.backtest_summary()
        if bs is None:
            self.skipTest("backtest parquet 缺失")
        self.assertIn("limitations", bs)
        self.assertIn("per_year", bs)
        lim = bs["limitations"]
        self.assertGreater(lim["draw_actual_rate"], 0.0)         # 实际有平局
        self.assertLess(lim["prod_draw_recall"], 0.15)           # 生产变体平局 recall 低(结构盲点)
        self.assertIn("2018", bs["per_year"])
        self.assertIn("2022", bs["per_year"])
        # 三变体每个都该有 accuracy/brier
        for v in ("elo", "dc", "dcs"):
            self.assertIn("accuracy", bs["per_year"]["2018"][v])


if __name__ == "__main__":
    unittest.main()

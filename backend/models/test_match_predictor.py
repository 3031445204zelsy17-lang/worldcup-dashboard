"""
P0-6 单场预测管线 单元测试  (标准库 unittest + numpy)
==========================================
手算/手设验证:
  1. top_scores: 排序降序 + 首项=全局最大格
  2. expected_goals: τ 修正后边际正确
  3. predict_match: 字段齐全 + 三概率和=1 + top_scores 非空
  4. from_artifacts 往返: 小 fit 存盘 → 加载 → 参数/predict 一致(无需 refit)
  5. wc_neutral_host: 非东道主/单东道主/双东道主 各情形
  6. WCPredictor 集成(真实产出): Spain-Japan 中立 / US 本土非中立

运行: .venv/bin/python -m unittest backend.models.test_match_predictor
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from dixon_coles import DixonColes  # noqa: E402
from match_predictor import (  # noqa: E402
    top_scores, expected_goals, predict_match, wc_neutral_host,
    WCPredictor, DEFAULT_DC_PARQUET, DEFAULT_GROUPS_CSV,
)


# —— 手设模型(隔离优化器, 测纯管线数学) ——
def _handmade_model() -> DixonColes:
    m = DixonColes()
    m.mu, m.gamma, m.gamma_host, m.rho = 0.4, 0.2, 0.0, -0.05
    m.attack = {"Strong": 1.5, "Weak": 0.7}
    m.defense = {"Strong": 1.4, "Weak": 0.8}
    m.teams = ["Strong", "Weak"]
    m.neff = {"Strong": 10.0, "Weak": 10.0}
    m._fitted = True
    return m


# —— 极简联赛(给 from_artifacts 往返测试用) ——
def _tiny_league() -> pd.DataFrame:
    rows = []
    teams = ["A", "B", "C"]
    # 每对主客各踢 4 场, A 最强(多进球), 给稳定可识别拟合
    strengths = {"A": (3, 0), "B": (1, 1), "C": (0, 2)}
    for home in teams:
        for away in teams:
            if home == away:
                continue
            hs, as_ = strengths[home][0], strengths[home][1]
            for _ in range(4):
                rows.append({"date": "2024-06-01", "home_team": home, "away_team": away,
                             "home_score": hs, "away_score": as_,
                             "tournament": "Friendly", "city": "X", "country": home,
                             "neutral": False, "year": 2024})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


class TestTopScores(unittest.TestCase):
    def test_sorted_desc_and_max_first(self):
        # 构造一个已知矩阵: (2,1) 格最大
        m = np.zeros((5, 5))
        m[2, 1] = 0.30
        m[1, 1] = 0.20
        m[0, 0] = 0.15
        m[3, 0] = 0.10
        ts = top_scores(m, n=3)
        self.assertEqual(len(ts), 3)
        self.assertEqual(ts[0], (2, 1, 0.30))   # 首项=全局最大
        self.assertTrue(all(ts[i][2] >= ts[i + 1][2] for i in range(len(ts) - 1)))

    def test_n_capped(self):
        m = np.full((3, 3), 1.0 / 9)   # 均匀 9 格
        ts = top_scores(m, n=5)
        self.assertEqual(len(ts), 5)            # 不足 n 时返回全部? 9>5 → 5
        self.assertEqual(len(top_scores(m, n=20)), 9)  # 超过格子数 → 截到 9

    def test_probs_in_unit(self):
        m = np.outer([0.5, 0.3, 0.2], [0.4, 0.35, 0.25])
        for _, _, p in top_scores(m, n=9):
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)


class TestExpectedGoals(unittest.TestCase):
    def test_marginals(self):
        # 手设矩阵: home 边际 = [0.5, 0.3, 0.2] → E=0*0.5+1*0.3+2*0.2=0.7
        # away 边际 = [0.4, 0.35, 0.25] → E=0*0.4+1*0.35+2*0.25=0.85
        m = np.outer([0.5, 0.3, 0.2], [0.4, 0.35, 0.25])
        eh, ea = expected_goals(m)
        self.assertAlmostEqual(eh, 0.70, places=6)
        self.assertAlmostEqual(ea, 0.85, places=6)

    def test_symmetric_matrix_equal_expectations(self):
        rng = np.random.default_rng(0)
        m = rng.random((6, 6))
        m = (m + m.T) / 2                       # 对称化 → home/away 期望相等
        m = m / m.sum()
        eh, ea = expected_goals(m)
        self.assertAlmostEqual(eh, ea, places=6)


class TestPredictMatch(unittest.TestCase):
    def setUp(self):
        self.m = _handmade_model()

    def test_fields_present(self):
        r = predict_match(self.m, "Strong", "Weak", neutral=True)
        for k in ["home", "away", "neutral", "host_home", "host_away",
                  "home_win", "draw", "away_win", "lambda_home", "lambda_away",
                  "expected_home", "expected_away", "top_scores", "score_matrix"]:
            self.assertIn(k, r)

    def test_probabilities_sum_to_one(self):
        r = predict_match(self.m, "Strong", "Weak", neutral=True)
        self.assertAlmostEqual(r["home_win"] + r["draw"] + r["away_win"], 1.0, places=6)

    def test_top_scores_nonempty_and_consistent(self):
        r = predict_match(self.m, "Strong", "Weak", neutral=True, top_n=5)
        self.assertEqual(len(r["top_scores"]), 5)
        # Top-N 之和 ≤ 1
        self.assertLessEqual(sum(p for _, _, p in r["top_scores"]), 1.0 + 1e-9)

    def test_home_advantage_raises_win(self):
        # neutral=False → home 享 γ → home_win 上升
        r_neu = predict_match(self.m, "Strong", "Weak", neutral=True)
        r_home = predict_match(self.m, "Strong", "Weak", neutral=False)
        self.assertGreater(r_home["home_win"], r_neu["home_win"])


class TestFromArtifactsRoundtrip(unittest.TestCase):
    """小 fit → 存盘 → from_artifacts 加载 → 参数与 predict 应与内存模型一致."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.parquet = Path(cls.tmp) / "dc.parquet"
        cls.jsonf = Path(cls.tmp) / "dc.json"
        cls.df = _tiny_league()
        cls.model = DixonColes(half_life_days=3650).fit(cls.df)
        cls.model.to_frame().to_parquet(cls.parquet, index=False)
        with open(cls.jsonf, "w") as f:
            json.dump(cls.model.global_params(), f)

    def test_loaded_predicts_identically(self):
        m2 = DixonColes.from_artifacts(self.parquet, self.jsonf)
        self.assertTrue(m2._fitted)
        # 全局参数一致
        for a in ["mu", "gamma", "gamma_host", "rho"]:
            self.assertAlmostEqual(getattr(m2, a), getattr(self.model, a), places=8)
        # 队参数一致
        for t in self.model.teams:
            self.assertAlmostEqual(m2.attack[t], self.model.attack[t], places=8)
            self.assertAlmostEqual(m2.defense[t], self.model.defense[t], places=8)
        # predict 一致
        p1 = self.model.predict("A", "B", neutral=False)
        p2 = m2.predict("A", "B", neutral=False)
        self.assertAlmostEqual(p1["home_win"], p2["home_win"], places=8)
        self.assertAlmostEqual(p1["lambda_home"], p2["lambda_home"], places=8)

    def test_loaded_neff_restored(self):
        m2 = DixonColes.from_artifacts(self.parquet, self.jsonf)
        self.assertEqual(set(m2.neff), set(self.model.neff))
        for t in self.model.neff:
            self.assertAlmostEqual(m2.neff[t], self.model.neff[t], places=8)

    def test_meta_restored(self):
        m2 = DixonColes.from_artifacts(self.parquet, self.jsonf)
        self.assertEqual(m2.use_importance_weight, self.model.use_importance_weight)
        self.assertEqual(m2.half_life_days, self.model.half_life_days)


class TestWcNeutralHost(unittest.TestCase):
    """WC 中立场/东道主判定(纯函数)."""

    def test_no_host_is_neutral(self):
        self.assertEqual(wc_neutral_host("Spain", "Japan"), (True, False, False))

    def test_home_host_not_neutral(self):
        # 美国本土 vs 非东道主 → 非中立, host_home=True
        neutral, hh, ha = wc_neutral_host("United States", "Switzerland")
        self.assertFalse(neutral)
        self.assertTrue(hh)
        self.assertFalse(ha)

    def test_away_host_not_neutral(self):
        neutral, hh, ha = wc_neutral_host("Spain", "Mexico")
        self.assertFalse(neutral)
        self.assertFalse(hh)
        self.assertTrue(ha)

    def test_both_hosts_neutral(self):
        # 双东道主(美 vs 加) → 主场对消 → 中立
        neutral, hh, ha = wc_neutral_host("United States", "Canada")
        self.assertTrue(neutral)
        self.assertTrue(hh)
        self.assertTrue(ha)


@unittest.skipUnless(DEFAULT_DC_PARQUET.exists() and DEFAULT_GROUPS_CSV.exists(),
                     "需要 P0-2/3/5 产出(dixon_coles_current.parquet + groups.csv)")
class TestWCPredictorIntegration(unittest.TestCase):
    """真实产出集成: from_artifacts + 分组 + 自动 neutral/host."""

    @classmethod
    def setUpClass(cls):
        cls.wp = WCPredictor()

    def test_neutral_match(self):
        r = self.wp.predict("Spain", "Japan")
        self.assertAlmostEqual(r["home_win"] + r["draw"] + r["away_win"], 1.0, places=5)
        self.assertTrue(r["neutral"])              # 都非东道主 → 中立
        self.assertFalse(r["host_home"])
        self.assertFalse(r["host_away"])

    def test_host_home_gets_gamma(self):
        # 美国 vs 非东道主 → neutral=False(享 γ) → 比 neutral=True 胜率高
        r_home = self.wp.predict("United States", "Japan")
        r_neu = self.wp.predict("United States", "Japan", host_home=False, host_away=False)
        self.assertFalse(r_home["neutral"])
        self.assertTrue(r_home["host_home"])
        self.assertGreater(r_home["home_win"], r_neu["home_win"])   # γ 抬升胜率

    def test_unknown_team_handled(self):
        # 未在历史数据的队 → predict 不抛错(默认 attack=defense=1)
        r = self.wp.predict("Spain", "Mars")
        self.assertAlmostEqual(r["home_win"] + r["draw"] + r["away_win"], 1.0, places=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

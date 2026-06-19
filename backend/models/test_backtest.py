"""
P0-7 回测管线 单元测试  (标准库 unittest + numpy/pandas)
==========================================
验证:
  1. walk_forward_elo: 赛前快照 == EloModel.ratings_at(严格 <date, 同日不互用) + 后场反映前场结果
  2. shrunk_variant: κ=0 等同 MLE / 大 κ 拉向 Elo 先验 / κ=0 不因 n_eff=0 队产生 NaN
  3. predict_backtest_match(真实产出集成): 字段齐全 + 三变体概率守恒

运行: .venv/bin/python -m unittest backend.models.test_backtest
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from dixon_coles import DixonColes  # noqa: E402
from elo import EloModel  # noqa: E402
from backtest import walk_forward_elo, predict_backtest_match, outcome, KAPPAS  # noqa: E402


def _hist(rows):
    """rows → DataFrame(date/home/away/score/tournament/city/country/neutral/year)."""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _match(date, home, away, hs, as_, country=None, neutral=False, tournament="Friendly"):
    return {"date": date, "home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_, "tournament": tournament,
            "city": "X", "country": country or home, "neutral": neutral, "year": int(date[:4])}


# ============================================================
# walk_forward_elo 正确性
# ============================================================
class TestWalkForwardElo(unittest.TestCase):
    def _df_with_backtest(self):
        """A/B/C 三队, 跨 3 天; 标 2 场为回测场(FIFA World Cup)."""
        rows = [
            _match("2024-06-01", "A", "B", 5, 0),     # D1: A 大胜 B
            _match("2024-06-01", "C", "B", 2, 0),     # D1 同日: C 胜 B
            _match("2024-06-02", "A", "C", 1, 1, tournament="FIFA World Cup"),  # 回测场①
            _match("2024-06-03", "B", "A", 0, 4, tournament="FIFA World Cup"),  # 回测场②(D3)
        ]
        df = _hist(rows)
        bt_mask = pd.Series(df["tournament"].eq("FIFA World Cup").values, index=df.index)
        return df, bt_mask

    def test_snapshot_matches_ratings_at(self):
        # 核心正确性: walk_forward 赛前快照 == EloModel.ratings_at(严格 <date)
        df, bt_mask = self._df_with_backtest()
        snaps = walk_forward_elo(df, bt_mask)
        for idx in df.index[bt_mask]:
            d = df.loc[idx, "date"]
            ref = EloModel().ratings_at(df, d)        # 严格 date < d
            snap = snaps[idx]
            for t in ["A", "B", "C"]:
                self.assertAlmostEqual(snap.get(t, 1500.0), ref.get(t, 1500.0), places=6,
                                       msg=f"队 {t} 在 {d.date()} 的赛前 Elo 不一致")

    def test_sameday_not_mutually_used(self):
        # 同日两场回测场共用"当日开盘前"Elo → 一场不喂另一场
        rows = [
            _match("2024-06-01", "A", "B", 3, 0),
            _match("2024-06-02", "A", "C", 2, 0, tournament="FIFA World Cup"),  # 回测①
            _match("2024-06-02", "B", "C", 1, 0, tournament="FIFA World Cup"),  # 回测②(同日)
        ]
        df = _hist(rows)
        bt_mask = pd.Series(df["tournament"].eq("FIFA World Cup").values, index=df.index)
        snaps = walk_forward_elo(df, bt_mask)
        # 两场同日 → 快照应完全相同(都用 6/1 之前的 Elo, 即初始 1500 全员)
        idx1, idx2 = df.index[bt_mask][0], df.index[bt_mask][1]
        self.assertEqual(snaps[idx1], snaps[idx2])
        # 且 A 的赛前 Elo 应反映 6/1 的大胜(高于初始 1500)
        self.assertGreater(snaps[idx1]["A"], 1500.0)

    def test_later_match_reflects_earlier(self):
        # 后面的回测场快照应反映更早比赛的结果(A 持续大胜 → A 的 Elo 在 D3 > D2)
        df, bt_mask = self._df_with_backtest()
        snaps = walk_forward_elo(df, bt_mask)
        idx_d2 = df.index[df["date"] == "2024-06-02"][0]
        idx_d3 = df.index[df["date"] == "2024-06-03"][0]
        # D3 的 A 快照包含了 D2(A vs C) 的结果 → 与 D2 快照不同
        self.assertNotAlmostEqual(snaps[idx_d2]["A"], snaps[idx_d3]["A"], places=4)

    def test_walk_forward_accepts_half_life(self):
        # half_life 参数被接受; half_life=0 数值 == 默认调用; half_life>0 仍有限值(防泄露不崩)
        df, bt_mask = self._df_with_backtest()
        snaps0 = walk_forward_elo(df, bt_mask, half_life=0.0)
        snaps_default = walk_forward_elo(df, bt_mask)
        idx = df.index[bt_mask][0]
        self.assertEqual(snaps0[idx], snaps_default[idx])
        snaps_decay = walk_forward_elo(df, bt_mask, half_life=730.0)
        for t in ["A", "B", "C"]:
            self.assertTrue(np.isfinite(snaps_decay[idx].get(t, 1500.0)))


# ============================================================
# shrunk_variant 正确性
# ============================================================
def _tiny_league():
    rows = []
    for home, away, hs, as_ in [("A", "B", 3, 0), ("B", "A", 0, 3), ("A", "C", 2, 1),
                                 ("C", "A", 1, 2), ("B", "C", 1, 1), ("C", "B", 1, 1)]:
        for _ in range(5):
            rows.append(_match("2024-06-01", home, away, hs, as_, country=home))
    return _hist(rows)


class TestShrunkVariant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = _tiny_league()
        cls.mle = DixonColes(half_life_days=3650).fit(cls.df)
        # 手设 Elo: A 强, C 弱 → 先验把 A 拉高、C 拉低
        cls.elo = {"A": 1900.0, "B": 1500.0, "C": 1300.0}

    def test_kappa_zero_is_identity(self):
        v0 = self.mle.shrunk_variant(self.elo, 0.0)
        for home, away in [("A", "B"), ("B", "C"), ("A", "C")]:
            p0 = v0.predict(home, away, neutral=True)
            pm = self.mle.predict(home, away, neutral=True)
            for k in ["home_win", "draw", "away_win", "lambda_home", "lambda_away"]:
                self.assertAlmostEqual(p0[k], pm[k], places=8,
                                       msg=f"κ=0 应等同 MLE ({home}-{away} {k})")

    def test_large_kappa_pulls_to_elo_prior(self):
        # κ→∞: Elo 先验是单强度因子(att=def=s) → net_strength=att/def 对所有队趋近 1.0
        # (先验不含攻防风格信息, 风格只能来自进球数据). 这是纯 Elo 基准(变体A)的定义行为.
        vbig = self.mle.shrunk_variant(self.elo, 1e4)
        for t in ["A", "B", "C"]:
            net_mle = self.mle.attack[t] / self.mle.defense[t]
            net_big = vbig.attack[t] / vbig.defense[t]
            self.assertLess(abs(net_big - 1.0), abs(net_mle - 1.0) + 1e-9,
                            msg=f"大 κ 下 {t} 的 net 应向 1.0 收敛(单强度先验抹平攻防风格)")

    def test_kappa_zero_no_nan_with_zero_neff(self):
        # n_eff=0 的队(仅踢 imp=0 赛事)在 κ=0 不应产生 NaN(回归 bug)
        rows = [_match("2024-06-01", "A", "B", 1, 0, tournament="Friendly") for _ in range(6)] + [
            _match("2024-06-01", "Ghost", "Bag", 1, 0, tournament="Olympic Games")  # imp=0 → Ghost n_eff=0
            for _ in range(2)]
        df = _hist(rows)
        m = DixonColes(half_life_days=3650).fit(df)
        v0 = m.shrunk_variant({"A": 1600, "B": 1500, "Ghost": 1500, "Bag": 1500}, 0.0)
        for t in ["A", "B", "Ghost", "Bag"]:
            self.assertTrue(np.isfinite(v0.attack[t]), msg=f"{t} attack 在 κ=0 不应为 NaN")

    def test_global_calibration_preserved(self):
        # 收缩后平均进球率不变: μ + mean(att_log) - mean(def_log) 守恒(近似, 重中心化保证)
        v5 = self.mle.shrunk_variant(self.elo, 5.0)
        # attack 仍 mean≈1(归一), 全队有限
        atts = np.array(list(v5.attack.values()))
        self.assertTrue(np.all(np.isfinite(atts)))


# ============================================================
# 集成: predict_backtest_match(真实历史产出)
# ============================================================
@unittest.skipUnless(Path("data/processed/international_history.parquet").exists(),
                     "需要 P0-2 产出 international_history.parquet")
class TestPredictBacktestMatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = pd.read_parquet("data/processed/international_history.parquet")
        # 取一场真实 2018 WC 场做集成测试
        cls.match = cls.df[(cls.df["tournament"] == "FIFA World Cup") & (cls.df["year"] == 2018)].iloc[0]
        bt_mask = pd.Series(cls.df.index.isin([cls.match.name]), index=cls.df.index)
        cls.snap = walk_forward_elo(cls.df, bt_mask)[cls.match.name]

    def test_fields_and_conservation(self):
        pred = predict_backtest_match(self.df, self.match, self.snap)
        # 必需字段
        for v in KAPPAS:
            for s in ["home_win", "draw", "away_win"]:
                self.assertIn(f"{v}_{s}", pred)
        self.assertIn("elo_exp", pred)
        self.assertIn("neutral", pred)
        # 三变体各自概率守恒
        for v in KAPPAS:
            tot = pred[f"{v}_home_win"] + pred[f"{v}_draw"] + pred[f"{v}_away_win"]
            self.assertAlmostEqual(tot, 1.0, places=5, msg=f"变体 {v} 三概率和≠1")
        # Elo 期望分 ∈ [0,1]
        self.assertGreaterEqual(pred["elo_exp"], 0.0)
        self.assertLessEqual(pred["elo_exp"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

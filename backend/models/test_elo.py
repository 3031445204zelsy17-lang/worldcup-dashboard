"""
P0-3 Elo 单元测试  (标准库 unittest, 零依赖)
==========================================
手算关键用例, 验证:
  1. 纯函数数值正确 (expected / goal_multiplier / k_factor / result_value)
  2. 主场优势含高原修正 (home_advantage_for: 海平面/中立场/高原分级正确)
  3. 单场更新 ΔR = K·G·(W−We) 对得上手算, 且高原赢球获得更少 Elo(修正方向对)
  4. 零和守恒 (主队+Δ == 客队−Δ)
  5. ratings_at 防数据泄露 (严格排除 as_of 当天及之后的比赛)

运行: .venv/bin/python backend/models/test_elo.py
"""
import sys
import unittest
from pathlib import Path

# 让 `from elo import ...` 在任意 cwd 下都能跑(测试文件与 elo.py 同目录)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from elo import (  # noqa: E402
    EloModel,
    expected,
    goal_multiplier,
    home_advantage_for,
    k_factor,
    result_value,
)


class TestExpected(unittest.TestCase):
    def test_equal_opponents_neutral(self):
        self.assertAlmostEqual(expected(1500, 1500, 0.0), 0.5)

    def test_equal_opponents_home_adv(self):
        # 等分 + 主场 65 分 → ~0.5925
        self.assertAlmostEqual(expected(1500, 1500, 65.0), 0.59245, places=4)

    def test_400_point_gap(self):
        # 差 400 分(中立场) → 经典 ~0.909 (1:10 反比)
        self.assertAlmostEqual(expected(1900, 1500, 0.0), 0.90909, places=4)
        self.assertAlmostEqual(expected(1500, 1900, 0.0), 0.09091, places=4)


class TestGoalMultiplier(unittest.TestCase):
    def test_small_diffs(self):
        self.assertEqual(goal_multiplier(0), 1.0)
        self.assertEqual(goal_multiplier(1), 1.0)
        self.assertEqual(goal_multiplier(-1), 1.0)

    def test_two_goal_margin(self):
        self.assertEqual(goal_multiplier(2), 1.5)
        self.assertEqual(goal_multiplier(-2), 1.5)

    def test_big_margin_formula(self):
        self.assertAlmostEqual(goal_multiplier(3), 1.75)   # 14/8
        self.assertAlmostEqual(goal_multiplier(4), 1.875)  # 15/8
        self.assertAlmostEqual(goal_multiplier(5), 2.0)    # 16/8


class TestKFactor(unittest.TestCase):
    def test_each_tier(self):
        self.assertEqual(k_factor("FIFA World Cup"), 60)                  # 世界杯正赛
        self.assertEqual(k_factor("UEFA Euro"), 50)                       # 洲际正赛
        self.assertEqual(k_factor("Copa América"), 50)
        self.assertEqual(k_factor("FIFA World Cup qualification"), 40)    # 预选赛(关键词)
        self.assertEqual(k_factor("UEFA Euro qualification"), 40)
        self.assertEqual(k_factor("Friendly"), 20)                        # 友谊赛

    def test_fallback_bucket(self):
        # 119 种小赛事都应落 30 档, 不漏
        for t in ["CECAFA Cup", "Merdeka Tournament", "King's Cup",
                  "UEFA Nations League", "Muratti Vase"]:
            self.assertEqual(k_factor(t), 30, msg=f"{t} 应兜底到 30")


class TestHomeAdvantage(unittest.TestCase):
    def test_neutral_is_zero(self):
        # 中立场一律 0, 即使是高原城市
        self.assertEqual(home_advantage_for("La Paz", True), 0.0)

    def test_sea_level_default(self):
        # 普通海平面主场 = 基础 65
        self.assertEqual(home_advantage_for("London", False), 65.0)
        self.assertEqual(home_advantage_for("Barranquilla", False), 65.0)  # 哥伦比亚真实主场

    def test_unknown_city_treated_sea_level(self):
        self.assertEqual(home_advantage_for("Mars City", False), 65.0)
        self.assertEqual(home_advantage_for(None, False), 65.0)

    def test_altitude_increases_advantage(self):
        # 高原主场 H > 65
        self.assertGreater(home_advantage_for("La Paz", False), 65.0)
        self.assertGreater(home_advantage_for("Quito", False), 65.0)

    def test_higher_altitude_higher_advantage(self):
        # 海拔越高, H 越大
        h_el_alto = home_advantage_for("El Alto", False)   # 4100m
        h_la_paz = home_advantage_for("La Paz", False)      # 3600m
        h_quito = home_advantage_for("Quito", False)        # 2850m
        self.assertGreater(h_el_alto, h_la_paz)
        self.assertGreater(h_la_paz, h_quito)

    def test_la_paz_value(self):
        # La Paz 3600m: 65 + 50×(3600-1500)/1000 = 65 + 105 = 170
        self.assertAlmostEqual(home_advantage_for("La Paz", False), 170.0, places=2)


class TestResultValue(unittest.TestCase):
    def test_win_draw_loss(self):
        self.assertEqual(result_value(2, 0), 1.0)
        self.assertEqual(result_value(1, 1), 0.5)
        self.assertEqual(result_value(0, 1), 0.0)


class TestSingleUpdate(unittest.TestCase):
    def test_friendly_home_win_sea_level(self):
        # 两队 1500, 友谊赛, 主队 2:0 胜, 海平面主场(H=65)
        #   We = expected(1500,1500,65) = 0.59245
        #   Δ  = K·G·(W−We) = 20 · 1.5 · (1 − 0.59245) = 12.2265
        m = EloModel()
        m._apply_one("A", "B", 2, 0, "Friendly", False, "London")
        self.assertAlmostEqual(m.get("A"), 1512.2265, places=2)
        self.assertAlmostEqual(m.get("B"), 1487.7735, places=2)

    def test_altitude_home_win_smaller_elo_gain(self):
        # 同样 2:0 友谊赛, 但在 La Paz(H=170): We 更高 → ΔR 更小 → 主队涨得更少
        #   We = expected(1500,1500,170) = 0.7269
        #   Δ  = 20 · 1.5 · (1 − 0.7269) = 8.193
        m_alt = EloModel()
        m_alt._apply_one("A", "B", 2, 0, "Friendly", False, "La Paz")
        self.assertAlmostEqual(m_alt.get("A"), 1508.19, places=1)

        # 对照: 海平面主队涨到 1512.23, 高原主队只涨到 1508.19 → 高原水分被修正
        m_sea = EloModel()
        m_sea._apply_one("A", "B", 2, 0, "Friendly", False, "London")
        self.assertLess(m_alt.get("A"), m_sea.get("A"))

    def test_neutral_no_home_advantage(self):
        # 中立场 2:0: We=0.5, Δ=20·1.5·0.5=15.0 (城市无影响)
        m = EloModel()
        m._apply_one("A", "B", 2, 0, "Friendly", True, "La Paz")
        self.assertAlmostEqual(m.get("A"), 1515.0, places=2)
        self.assertAlmostEqual(m.get("B"), 1485.0, places=2)

    def test_zero_sum_conservation(self):
        # 任意比赛, 双方 rating 变化互为相反数 → 总和守恒(高原 H 也满足零和)
        m = EloModel()
        m._apply_one("A", "B", 3, 1, "FIFA World Cup", False, "Quito")
        self.assertAlmostEqual(m.get("A") + m.get("B"), 3000.0, places=6)

    def test_world_cup_bigger_step_than_friendly(self):
        # 同样 2:0 中立场, 世界杯(K=60)的步长应明显大于友谊赛(K=20)
        m_wc = EloModel()
        m_wc._apply_one("A", "B", 2, 0, "FIFA World Cup", True, None)
        m_fr = EloModel()
        m_fr._apply_one("A", "B", 2, 0, "Friendly", True, None)
        self.assertGreater(abs(m_wc.get("A") - 1500), abs(m_fr.get("A") - 1500))


class TestRatingsAtLeakage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 两场 A vs B: 第一场 2020 海平面, 第二场 2021 中立
        cls.df = pd.DataFrame([
            {"date": "2020-01-01", "home_team": "A", "away_team": "B",
             "home_score": 2, "away_score": 0, "tournament": "Friendly",
             "neutral": False, "city": "London"},
            {"date": "2021-01-01", "home_team": "A", "away_team": "B",
             "home_score": 3, "away_score": 0, "tournament": "Friendly",
             "neutral": True, "city": "Madrid"},
        ])
        cls.df["date"] = pd.to_datetime(cls.df["date"])

    def test_snapshot_excludes_future_matches(self):
        m = EloModel().fit(self.df)
        full = m.get("A")
        snap = EloModel().ratings_at(self.df, "2020-06-01")
        first_only = snap["A"]
        self.assertAlmostEqual(first_only, 1512.2265, places=2)  # 单场海平面手算值
        self.assertNotAlmostEqual(full, first_only, places=2)
        self.assertGreater(full, first_only)

    def test_snapshot_strict_before_cutoff(self):
        # cutoff = 第二场当天: 严格 <, 不含当天 → 仅第一场
        snap = EloModel().ratings_at(self.df, "2021-01-01")
        self.assertAlmostEqual(snap["A"], 1512.2265, places=2)


# ============================================================
# 时间衰减 half_life (修 Elo 滞后, 默认关)
# ============================================================
class TestTimeDecay(unittest.TestCase):
    def test_half_life_zero_no_decay(self):
        # half_life=0 → 不衰减, 等同不传日期参数的调用
        m1 = EloModel(); m1._apply_one("A", "B", 3, 0, "FIFA World Cup", False, "X",
                                       match_date="2024-01-01", ref_date="2026-01-01", half_life=0.0)
        m2 = EloModel(); m2._apply_one("A", "B", 3, 0, "FIFA World Cup", False, "X")
        self.assertAlmostEqual(m1.get("A"), m2.get("A"), places=6)
        self.assertAlmostEqual(m1.get("B"), m2.get("B"), places=6)

    def test_decay_shrinks_old_match_delta(self):
        # 同样比赛, match_date 离 ref_date 越远(越旧) → delta 衰减越多 → A 偏离 1500 越小
        m_recent = EloModel(); m_recent._apply_one("A", "B", 3, 0, "FIFA World Cup", False, "X",
                                                   match_date="2025-12-01", ref_date="2026-01-01", half_life=365.0)
        m_old = EloModel(); m_old._apply_one("A", "B", 3, 0, "FIFA World Cup", False, "X",
                                             match_date="2024-01-01", ref_date="2026-01-01", half_life=365.0)
        self.assertLess(abs(m_old.get("A") - 1500.0), abs(m_recent.get("A") - 1500.0))

    def test_decay_preserves_zero_sum(self):
        # 衰减后双方仍零和: A 增量 == -B 增量(双方乘同一权重)
        m = EloModel(); m._apply_one("A", "B", 3, 0, "FIFA World Cup", False, "X",
                                     match_date="2024-01-01", ref_date="2026-01-01", half_life=365.0)
        self.assertAlmostEqual(m.get("A") - 1500.0, -(m.get("B") - 1500.0), places=6)

    def test_ref_date_none_no_decay(self):
        # ref_date=None → 退化不衰减(即使 half_life>0)
        m1 = EloModel(); m1._apply_one("A", "B", 3, 0, "FIFA World Cup", False, "X",
                                       match_date="2024-01-01", ref_date=None, half_life=365.0)
        m2 = EloModel(); m2._apply_one("A", "B", 3, 0, "FIFA World Cup", False, "X")
        self.assertAlmostEqual(m1.get("A"), m2.get("A"), places=6)

    def test_ratings_at_half_life_decays_old(self):
        # ratings_at 带 half_life: 旧比赛贡献衰减 → 旧大胜的 A 比 no_decay 低
        rows = [
            {"date": "2022-01-01", "home_team": "A", "away_team": "B", "home_score": 5, "away_score": 0,
             "tournament": "FIFA World Cup", "neutral": False, "city": "X", "country": "A"},
            {"date": "2025-12-01", "home_team": "A", "away_team": "B", "home_score": 1, "away_score": 1,
             "tournament": "Friendly", "neutral": False, "city": "X", "country": "A"},
        ]
        df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"])
        no_decay = EloModel().ratings_at(df, "2026-01-01", half_life=0.0)
        decay = EloModel().ratings_at(df, "2026-01-01", half_life=365.0)
        self.assertLess(decay["A"], no_decay["A"], "旧大胜(2022)被衰减 → decay 的 A 应低于 no_decay")


if __name__ == "__main__":
    unittest.main(verbosity=2)

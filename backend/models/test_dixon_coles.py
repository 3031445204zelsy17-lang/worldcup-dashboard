"""
P0-4 Dixon-Coles 单元测试  (标准库 unittest + numpy)
==========================================
手算关键用例, 验证:
  1. 低分修正 τ 方向正确 (ρ<0 → 0-0/1-1 增大, 1-0/0-1 减小, 其余=1)
  2. 时间衰减 φ 半衰期准确 + 单调递减 + 当天权重=1
  3. predict 概率和=1, 三 outcome 和=1, 强队胜率高 (手设参数, 不依赖优化器)
  4. 主场 γ / 东道主 γ_host 加成方向正确 (胜率上升)
  5. fit 在小数据上收敛 + 参数量正确
  6. fit_at 防数据泄露 (严格 < cutoff, 早期大胜/后期大败的时点快照能区分)

运行: .venv/bin/python backend/models/test_dixon_coles.py
"""
import sys
import unittest
from pathlib import Path

# 让 `from dixon_coles import ...` 在任意 cwd 下都能跑(测试文件与实现同目录)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from dixon_coles import (  # noqa: E402
    DixonColes,
    importance_weight,
    _elo_prior_shrink,
    tau_correction,
    time_decay_weights,
)


class TestTauCorrection(unittest.TestCase):
    """Dixon-Coles 低分修正因子 τ 的方向与数值."""

    def test_rho_zero_is_identity(self):
        # ρ=0 → 所有比分 τ=1 (退化为纯独立 Poisson)
        h = np.array([0, 1, 2, 3])
        a = np.array([0, 1, 1, 0])
        lam_h = np.array([1.0, 1.5, 2.0, 0.5])
        lam_a = np.array([1.0, 0.8, 1.0, 1.2])
        tau = tau_correction(h, a, lam_h, lam_a, rho=0.0)
        np.testing.assert_allclose(tau, 1.0)

    def test_negative_rho_boosts_00_and_11(self):
        # ρ<0 → 0-0 与 1-1 概率被增大 (τ>1)
        rho = -0.1
        t00 = tau_correction(np.array([0]), np.array([0]),
                             np.array([1.0]), np.array([1.0]), rho)[0]
        t11 = tau_correction(np.array([1]), np.array([1]),
                             np.array([1.5]), np.array([0.8]), rho)[0]
        # (0,0): 1 − λh·λa·ρ = 1 − 1·1·(−0.1) = 1.1
        self.assertAlmostEqual(t00, 1.1, places=6)
        # (1,1): 1 − ρ = 1 − (−0.1) = 1.1
        self.assertAlmostEqual(t11, 1.1, places=6)
        self.assertGreater(t00, 1.0)
        self.assertGreater(t11, 1.0)

    def test_negative_rho_shrinks_10_and_01(self):
        # ρ<0 → 1-0 与 0-1 概率被减小 (τ<1)
        rho = -0.1
        t10 = tau_correction(np.array([1]), np.array([0]),
                             np.array([1.5]), np.array([1.0]), rho)[0]
        t01 = tau_correction(np.array([0]), np.array([1]),
                             np.array([1.0]), np.array([0.8]), rho)[0]
        # (1,0): 1 + λa·ρ = 1 + 1.0·(−0.1) = 0.9
        self.assertAlmostEqual(t10, 0.9, places=6)
        # (0,1): 1 + λh·ρ = 1 + 1.0·(−0.1) = 0.9
        self.assertAlmostEqual(t01, 0.9, places=6)
        self.assertLess(t10, 1.0)
        self.assertLess(t01, 1.0)

    def test_high_scores_untouched(self):
        # 非 0/1 低分 → τ=1 (τ 只动 0-0/0-1/1-0/1-1 四格)
        rho = -0.15
        for h, a in [(2, 1), (1, 2), (3, 0), (0, 3), (2, 2)]:
            tau = tau_correction(np.array([h]), np.array([a]),
                                 np.array([1.5]), np.array([1.0]), rho)[0]
            self.assertAlmostEqual(tau, 1.0, places=6,
                                   msg=f"(h={h},a={a}) 不应被 τ 修正")

    def test_tau_never_nonpositive(self):
        # 极端 ρ 也不能让 τ≤0 (clip 保底), 防 log(负/0)
        h = np.zeros(5); a = np.zeros(5)
        lam_h = np.full(5, 3.0); lam_a = np.full(5, 3.0)
        for rho in [-0.2, 0.2, -0.5, 0.5]:
            tau = tau_correction(h, a, lam_h, lam_a, rho)
            self.assertTrue(np.all(tau > 0), msg=f"ρ={rho} 产生非正 τ")


class TestTimeDecayWeights(unittest.TestCase):
    """时间衰减 φ: 半衰期准确 + 单调递减."""

    def test_half_life_weight(self):
        # 正好一个半衰期(365 天间隔, 用平年避免闰年偏移)→ 权重=0.5
        w = time_decay_weights(pd.Series(["2021-01-01"]), "2022-01-01", 365.0)
        self.assertAlmostEqual(w[0], 0.5, places=4)

    def test_same_day_weight_one(self):
        # 比赛当天 Δt=0 → 权重=1
        w = time_decay_weights(pd.Series(["2022-01-01"]), "2022-01-01", 730.0)
        self.assertAlmostEqual(w[0], 1.0, places=6)

    def test_monotonic_decreasing(self):
        # 越老的比赛权重越低
        dates = pd.Series(["2024-01-01", "2022-01-01", "2020-01-01"])
        w = time_decay_weights(dates, "2024-01-01", 730.0)
        self.assertGreater(w[0], w[1])
        self.assertGreater(w[1], w[2])

    def test_future_clipped_to_one(self):
        # as_of 之后的比赛(不该出现在训练里, 兜底) Δt 被 clip 到 0 → 权重=1
        w = time_decay_weights(pd.Series(["2025-01-01"]), "2024-01-01", 730.0)
        self.assertAlmostEqual(w[0], 1.0, places=6)


# —— 手设参数的 model, 隔离优化器, 测 predict 的纯数学 ——
def _handmade_model() -> DixonColes:
    m = DixonColes()
    m.mu = 0.4
    m.gamma = 0.2
    m.gamma_host = 0.1
    m.rho = -0.05
    m.attack = {"Strong": 1.5, "Weak": 0.6}
    m.defense = {"Strong": 1.5, "Weak": 0.6}   # 防守好(def 大) → 对手少进球
    m.teams = ["Strong", "Weak"]
    m._fitted = True
    return m


class TestPredictMath(unittest.TestCase):
    """predict 的概率守恒与强弱方向(参数手设, 确定性强)."""

    def setUp(self):
        self.m = _handmade_model()

    def test_probabilities_sum_to_one(self):
        p = self.m.predict("Strong", "Weak", neutral=True)
        # 比分矩阵和=1; 三 outcome 和=1
        self.assertAlmostEqual(p["score_matrix"].sum(), 1.0, places=6)
        self.assertAlmostEqual(p["home_win"] + p["draw"] + p["away_win"], 1.0, places=6)

    def test_strong_beats_weak(self):
        p = self.m.predict("Strong", "Weak", neutral=True)
        # Strong(att=1.5,def=1.5) vs Weak(att=0.6,def=0.6):
        #   λ_h = e^0.4·1.5·(1/0.6) ≈ 3.73,  λ_a = e^0.4·0.6·(1/1.5) ≈ 0.60 → 强队大优
        self.assertGreater(p["lambda_home"], p["lambda_away"])
        self.assertGreater(p["home_win"], 0.70)

    def test_symmetric_when_equal(self):
        # 两队参数相同 → 中立场 50/50: home_win==away_win, 且 2·home_win+draw=1
        p = self.m.predict("Strong", "Strong", neutral=True)
        self.assertAlmostEqual(p["home_win"], p["away_win"], places=4)
        self.assertAlmostEqual(2 * p["home_win"] + p["draw"], 1.0, places=4)

    def test_unknown_team_defaults_to_average(self):
        # 未见队按 attack=defense=1(均值)处理, 不抛错
        p = self.m.predict("Strong", "Mars", neutral=True)
        self.assertAlmostEqual(p["score_matrix"].sum(), 1.0, places=6)


class TestPredictAdvantage(unittest.TestCase):
    """主场 γ 与东道主 γ_host 加成方向."""

    def setUp(self):
        self.m = _handmade_model()

    def test_home_advantage_raises_win_prob(self):
        # 普通主场(neutral=False)胜率 > 中立场(neutral=True)
        p_neu = self.m.predict("Strong", "Weak", neutral=True)
        p_home = self.m.predict("Strong", "Weak", neutral=False)
        self.assertGreater(p_home["lambda_home"], p_neu["lambda_home"])
        self.assertGreater(p_home["home_win"], p_neu["home_win"])

    def test_host_bonus_raises_win_prob(self):
        # 东道主加成(host_home=True)胜率 > 纯中立场
        p_neu = self.m.predict("Strong", "Weak", neutral=True, host_home=False)
        p_host = self.m.predict("Strong", "Weak", neutral=True, host_home=True)
        self.assertGreater(p_host["lambda_home"], p_neu["lambda_home"])
        self.assertGreater(p_host["home_win"], p_neu["home_win"])

    def test_host_bonus_on_top_of_home(self):
        # 东道主主场(neutral=False + host)λ 最大: γ + γ_host 同时生效
        p_home = self.m.predict("Strong", "Weak", neutral=False, host_home=False)
        p_full = self.m.predict("Strong", "Weak", neutral=False, host_home=True)
        self.assertGreater(p_full["lambda_home"], p_home["lambda_home"])


# —— 人造 4 队联赛: 前/后期实力反转, 用于测 fit 收敛与 fit_at 防泄露 ——
def _make_reversal_league() -> pd.DataFrame:
    """4 队(A/B/C/D). 2020 年 A 大胜所有人; 2021 年 A 大败给所有人.
    → 用 2020 拟合的 A.attack 应高于用 2020+2021 拟合的."""
    rows = []
    opps = ["B", "C", "D"]
    for yr, (hs, as_) in [(2020, (4, 0)), (2021, (0, 4))]:
        for opp in opps:
            for _ in range(5):  # 每对每年 5 场, 共 30 场
                rows.append({
                    "date": f"{yr}-06-01", "home_team": "A", "away_team": opp,
                    "home_score": hs, "away_score": as_,
                    "tournament": "Friendly", "city": "X", "country": "A", "neutral": False,
                    "year": yr,
                })
    # B/C/D 之间也踢几场, 给它们建立相对实力, 稳定拟合
    for yr in (2020, 2021):
        for home in ["B", "C"]:
            for away in ["C", "D"]:
                if home == away:
                    continue
                rows.append({
                    "date": f"{yr}-07-01", "home_team": home, "away_team": away,
                    "home_score": 1, "away_score": 1, "tournament": "Friendly",
                    "city": "Y", "country": home, "neutral": False, "year": yr,
                })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


class TestFitConvergence(unittest.TestCase):
    """小数据集 fit: 收敛 + 参数量正确 + 有限."""

    @classmethod
    def setUpClass(cls):
        cls.df = _make_reversal_league()

    def test_fit_returns_finite_params(self):
        m = DixonColes(half_life_days=3650).fit(self.df)
        self.assertTrue(m._fitted)
        self.assertEqual(len(m.attack), 4)
        self.assertEqual(len(m.defense), 4)
        for v in list(m.attack.values()) + list(m.defense.values()):
            self.assertTrue(np.isfinite(v))
            self.assertGreater(v, 0.0)
        for v in [m.mu, m.gamma, m.gamma_host, m.rho]:
            self.assertTrue(np.isfinite(v))

    def test_rho_within_bounds(self):
        m = DixonColes(half_life_days=3650).fit(self.df)
        self.assertGreaterEqual(m.rho, -0.2 - 1e-9)
        self.assertLessEqual(m.rho, 0.2 + 1e-9)

    def test_to_frame_columns(self):
        m = DixonColes(half_life_days=3650).fit(self.df)
        frame = m.to_frame()
        self.assertEqual(list(frame.columns),
                         ["team", "attack", "defense", "net_strength", "n_eff"])
        self.assertEqual(len(frame), 4)


class TestFitAtLeakage(unittest.TestCase):
    """fit_at 防泄露: 严格 < cutoff, 前/后期反转能被时点快照区分."""

    @classmethod
    def setUpClass(cls):
        cls.df = _make_reversal_league()

    def test_snapshot_uses_strictly_earlier(self):
        # cutoff = 2021-06-01(A 大败那批开始日): 严格 < 排掉所有 2021 比赛 → 只用 2020
        # cutoff = 2022-01-01: 含 2021 → A 的实力被拉低
        m_2020 = DixonColes(half_life_days=3650).fit_at(self.df, "2021-06-01")
        m_full = DixonColes(half_life_days=3650).fit_at(self.df, "2022-01-01")
        # 2020-only 拟合: A 大胜所有人 → A.attack 最高
        # 加入 2021(大败)→ A.attack 被拉低
        self.assertGreater(m_2020.attack["A"], m_full.attack["A"],
                           "fit_at 应排除 ≥ cutoff 的比赛; A 的早期强势应只反映在早快照里")

    def test_cutoff_on_match_day_excludes_it(self):
        # 严格 < cutoff: cutoff 当天的比赛被排除. 用一个"只在 cutoff 当天出场"的队灵敏检测——
        # 它在 fit_at(cutoff) 里不出现(被排除), 在 fit_at(cutoff+1天) 里出现(被纳入).
        rows = [
            {"date": "2021-06-01", "home_team": "A", "away_team": "B", "home_score": 2, "away_score": 1,
             "tournament": "Friendly", "city": "X", "country": "A", "neutral": False, "year": 2021},
            {"date": "2021-06-01", "home_team": "B", "away_team": "A", "home_score": 1, "away_score": 1,
             "tournament": "Friendly", "city": "X", "country": "B", "neutral": False, "year": 2021},
            {"date": "2021-07-01", "home_team": "C", "away_team": "A", "home_score": 0, "away_score": 3,
             "tournament": "Friendly", "city": "X", "country": "C", "neutral": False, "year": 2021},
        ]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        m_excl = DixonColes(half_life_days=3650).fit_at(df, "2021-07-01")   # 严格<7/01 → 排除 C 的当天场
        m_incl = DixonColes(half_life_days=3650).fit_at(df, "2021-07-02")   # 含 C 的当天场
        self.assertNotIn("C", m_excl.attack, "cutoff 当天的比赛应被严格排除 → C 不出现在拟合里")
        self.assertIn("C", m_incl.attack, "cutoff+1天 → C 的当天比赛被纳入")
        self.assertEqual(set(m_excl.teams), {"A", "B"})
        self.assertEqual(set(m_incl.teams), {"A", "B", "C"})


# ============================================================
# P0-5: 含金量分档 importance_weight
# ============================================================
class TestImportanceWeight(unittest.TestCase):
    """赛事类型 → 含金量权重 w 的分档正确性."""

    def test_tier_values(self):
        # 六档核心取值
        self.assertEqual(importance_weight("FIFA World Cup"), 1.00)
        self.assertAlmostEqual(importance_weight("UEFA Euro"), 0.90)
        self.assertAlmostEqual(importance_weight("Copa América"), 0.90)
        self.assertAlmostEqual(importance_weight("Gold Cup"), 0.90)
        # 预选赛(关键词, 大小写不敏感)
        self.assertAlmostEqual(importance_weight("FIFA World Cup qualification"), 0.75)
        self.assertAlmostEqual(importance_weight("AFC Asian Cup Qualification"), 0.75)
        # 次级正式(国家联赛 + 主要区域杯)
        self.assertAlmostEqual(importance_weight("UEFA Nations League"), 0.50)
        self.assertAlmostEqual(importance_weight("Gulf Cup"), 0.50)
        self.assertAlmostEqual(importance_weight("CECAFA Cup"), 0.50)

    def test_friendly_and_default_invitational(self):
        # 友谊赛 0.25; 兜底有名邀请杯(King's Cup/Kirin…)也 0.25
        self.assertAlmostEqual(importance_weight("Friendly"), 0.25)
        self.assertAlmostEqual(importance_weight("FIFA Series"), 0.25)
        self.assertAlmostEqual(importance_weight("King's Cup"), 0.25)
        self.assertAlmostEqual(importance_weight("Kirin Challenge Cup"), 0.25)
        self.assertAlmostEqual(importance_weight("Some Obscure Invitation Trophy"), 0.25)

    def test_olympic_and_games_excluded(self):
        # Olympic U-23 / 综合运动会 → 剔除(0): 阵容系统性非成年A队
        self.assertEqual(importance_weight("Olympic Games"), 0.00)
        self.assertEqual(importance_weight("Asian Games"), 0.00)
        self.assertEqual(importance_weight("Southeast Asian Games"), 0.00)
        self.assertEqual(importance_weight("Pacific Games"), 0.00)

    def test_amateur_nonfifa_excluded(self):
        # 非FIFA业余队(CONIFA/Viva/Island Games) → 剔除; 且先于预选赛判定
        self.assertEqual(importance_weight("CONIFA World Football Cup"), 0.00)
        self.assertEqual(importance_weight("CONIFA World Football Cup qualification"), 0.00)  # 防被当预选赛
        self.assertEqual(importance_weight("Viva World Cup"), 0.00)
        self.assertEqual(importance_weight("Island Games"), 0.00)
        self.assertEqual(importance_weight("Muratti Vase"), 0.00)

    def test_weight_in_unit_interval(self):
        for t in ["FIFA World Cup", "Friendly", "Olympic Games", "CONIFA Cup",
                  "UEFA Euro qualification", "Nonsense"]:
            w = importance_weight(t)
            self.assertGreaterEqual(w, 0.0)
            self.assertLessEqual(w, 1.0)


# ============================================================
# P0-5: Elo 先验收缩 _elo_prior_shrink (纯函数, 确定性强)
# ============================================================
class TestEloPriorShrink(unittest.TestCase):
    """post-hoc 收缩: 稀疏队拉向先验, 数据充足队不动, 全局校准保持."""

    def _three_team_setup(self):
        # 3 队: Flash(踢1场), Rock(踢10场), Bag(全踢). ih/ia/match_w 手设.
        teams = ["Flash", "Rock", "Bag"]
        idx = {"Flash": 0, "Rock": 1, "Bag": 2}
        # Flash vs Bag ×1; Rock vs Bag ×10
        ih = np.array([idx["Flash"]] + [idx["Rock"]] * 10)
        ia = np.array([idx["Bag"]] + [idx["Bag"]] * 10)
        match_w = np.ones(11)
        return teams, ih, ia, match_w

    def test_sparse_shrunk_more_than_dense(self):
        # Elo 全相等 → 先验 s=0(均值). Flash(离谱高 att, n_eff=1) 应被拉向 0 远比 Rock(n_eff=10) 狠.
        teams, ih, ia, match_w = self._three_team_setup()
        att_log = np.array([2.0, 1.5, 0.0])   # Flash/Rock 都偏高, Bag 均值
        def_log = np.array([0.0, 0.0, 0.0])
        elo = {"Flash": 1500.0, "Rock": 1500.0, "Bag": 1500.0}  # 等Elo → s=0
        att_shr, _, beta, neff = _elo_prior_shrink(teams, ih, ia, match_w, att_log, def_log, elo, kappa=5.0)
        # n_eff: Flash=1, Rock=10
        self.assertAlmostEqual(neff[0], 1.0)
        self.assertAlmostEqual(neff[1], 10.0)
        # 等Elo → beta≈0 → s=0 → att_shr = wd·att_log; Flash wd 小 → 拉向 0 更多
        self.assertLess(abs(att_shr[0] - 0.0), abs(att_log[0] - 0.0))   # Flash 向 0 靠
        self.assertGreater(abs(att_shr[0] - att_log[0]), abs(att_shr[1] - att_log[1]))  # Flash 移动更多
        self.assertAlmostEqual(beta, 0.0, places=6)

    def test_elo_scales_prior(self):
        # att 与 Elo 正相关 → 回归斜率 β>0, 先验 s 随 Elo 单调
        teams, ih, ia, match_w = self._three_team_setup()
        att_log = np.array([0.2, 1.5, 0.0])    # Rock(高Elo)att 高, Flash(低Elo)att 低
        def_log = np.array([0.0, 0.0, 0.0])
        elo = {"Flash": 1400.0, "Rock": 1900.0, "Bag": 1500.0}
        _, _, beta, _ = _elo_prior_shrink(teams, ih, ia, match_w, att_log, def_log, elo, kappa=5.0)
        self.assertGreater(beta, 0.0, "att 与 Elo 正相关时 β 应>0")
        e = np.array([1400.0, 1900.0, 1500.0]) - 1600.0
        s = beta * e
        self.assertGreater(s[1], s[2])   # Rock(高Elo) 先验 > Bag
        self.assertGreater(s[2], s[0])   # Bag 先验 > Flash(低Elo)

    def test_global_calibration_preserved(self):
        # 收缩后 attack 均值=0(归一), defense 均值=原 def_mean → 平均进球率不变
        teams, ih, ia, match_w = self._three_team_setup()
        att_log = np.array([1.5, 0.8, -0.3])
        def_log = np.array([0.4, -0.2, 0.1])
        elo = {"Flash": 1400.0, "Rock": 1900.0, "Bag": 1500.0}
        att_shr, def_shr, _, _ = _elo_prior_shrink(teams, ih, ia, match_w, att_log, def_log, elo, kappa=5.0)
        self.assertAlmostEqual(att_shr.mean(), 0.0, places=10)
        self.assertAlmostEqual(def_shr.mean(), def_log.mean(), places=10)

    def test_zero_neff_is_pure_prior(self):
        # n_eff=0(完全不出现的队) → wd=0 → 完全等于先验 s, MLE 离谱值被丢弃
        teams = ["Ghost", "Real", "Other"]
        ih = np.array([1, 1]); ia = np.array([2, 2])  # 只 Real vs Other; Ghost(0) 不参与 → n_eff=0
        match_w = np.array([1.0, 1.0])
        att_log = np.array([9.0, 0.0, 0.0])   # Ghost 的 MLE 离谱(纯函数测, 不经优化器)
        def_log = np.array([0.0, 0.0, 0.0])
        elo = {"Ghost": 1500.0, "Real": 1500.0, "Other": 1500.0}  # 等Elo → s=0
        att_shr, _, beta, neff = _elo_prior_shrink(teams, ih, ia, match_w, att_log, def_log, elo, kappa=5.0)
        self.assertAlmostEqual(neff[0], 0.0)
        self.assertAlmostEqual(beta, 0.0, places=6)   # 等Elo → β=0
        self.assertAlmostEqual(att_shr[0], 0.0, places=10)  # wd=0 → att_shr=s=0(先验), MLE 的 9 被完全丢弃


# ============================================================
# P0-5: fit() 收缩集成 —— 端到端验证稀疏队被拉向 Elo 先验
# ============================================================
def _make_flash_league() -> pd.DataFrame:
    """Flash: 低Elo, 只踢2场且都5-0大胜(运气) → 无收缩时 attack 虚高.
    Rock: 高Elo, 踢25场稳健 → 数据充足. Bag: 被虐背景板."""
    rows = []
    for _ in range(2):   # Flash vs Bag ×2, Flash 5-0
        rows.append({"date": "2024-06-01", "home_team": "Flash", "away_team": "Bag",
                     "home_score": 5, "away_score": 0, "tournament": "Friendly",
                     "city": "F", "country": "Flash", "neutral": False, "year": 2024})
    for _ in range(25):  # Rock vs Bag ×25, Rock 2-1
        rows.append({"date": "2024-06-01", "home_team": "Rock", "away_team": "Bag",
                     "home_score": 2, "away_score": 1, "tournament": "Friendly",
                     "city": "R", "country": "Rock", "neutral": False, "year": 2024})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


class TestFitShrinkageIntegration(unittest.TestCase):
    """fit(elo=...) 端到端: 稀疏低Elo队的虚高参数被 Elo 先验拉回."""

    @classmethod
    def setUpClass(cls):
        cls.df = _make_flash_league()
        # 手设 Elo: Flash 低(1400), Rock 高(1900), Bag 中(1500)
        cls.elo = {"Flash": 1400.0, "Rock": 1900.0, "Bag": 1500.0}

    def test_flash_inflated_without_shrink(self):
        # 无收缩: Flash 两场5-0 → attack 虚高(高于 Rock)
        m = DixonColes(half_life_days=3650).fit(self.df)   # 不传 elo → 纯MLE
        self.assertGreater(m.attack["Flash"], m.attack["Rock"],
                           "无收缩时 Flash 的2场运气大胜应虚高 attack")
        self.assertEqual(m.shrink_applied, False)

    def test_shrink_pulls_flash_down(self):
        # 有收缩: Flash(低Elo, n_eff≈0.5)被拉向低先验 → attack 显著低于无收缩
        m_unshr = DixonColes(half_life_days=3650).fit(self.df)
        m_shr = DixonColes(half_life_days=3650).fit(self.df, elo=self.elo)
        self.assertTrue(m_shr.shrink_applied)
        self.assertLess(m_shr.attack["Flash"], m_unshr.attack["Flash"],
                        "Elo先验应把虚高的 Flash attack 拉下来")
        # 稀疏队更信先验: 先验权重 wd=n_eff/(n_eff+κ), Flash n_eff 小 → wd 小.
        # (注: 强 κ 下"移动量"取决于 MLE-先验 gap 而非 n_eff, 故用 wd 这个稳健的权重指标)
        k = m_shr.shrinkage_kappa
        wd = lambda t: m_shr.neff[t] / (m_shr.neff[t] + k)
        self.assertLess(wd("Flash"), wd("Rock"), "稀疏队(Flash)应更信先验(wd 更小)")

    def test_neff_populated_and_flash_lowest(self):
        m = DixonColes(half_life_days=3650).fit(self.df, elo=self.elo)
        # Flash 踢2场(imp0.25) → n_eff≈0.5; Bag 踢27场 → n_eff 最大
        self.assertLess(m.neff["Flash"], m.neff["Bag"])
        self.assertLess(m.neff["Flash"], m.neff["Rock"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

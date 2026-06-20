"""
P2-1 赛中实时胜率模型
=====================
给定一场进行中比赛的 game state(当前比分 / 已踢分钟 / 红牌) → 剩余时间 Poisson 模拟 →
实时胜 / 平 / 负(+ 淘汰赛加时点球后胜出).

复用 mc.py 的 λ 公式: lam_h = exp(μ)·att[home]/def[away]·exp(γ·¬neutral), lam_a 同理.
与赛前 WCPredictor.predict 自洽: minute=0 + 0:0 + 0红牌 → λ_rem = λ_full(无缩放无修正).

公式
----
- λ_rem = λ_full × (90-minute)/90       剩余时间线性缩放(minute=0≈赛前, 90→0 锁定终场)
- 红牌修正(被罚方 n 张): 该队进攻 ×0.85**n, 对手进攻 ×1.20**n
  (少一人净负; 累乘 **n 边际递减稳于线性)
- 采样: Poisson × N(默认 20000) 剩余进球 + 当前比分 = 终场 → 胜 / 平 / 负
- 淘汰赛 90min 平 → 加时(λ_full × 1/3, 复用 mc.EXTRA_TIME_LAMBDA_FRACTION) → 仍平 50/50 点球

红牌系数 0.85/1.20 来自足球战术常识(少一人进攻资源降约 1/7≈0.85; 防守损失大于进攻损失 →
对手增益 1.20 > 0.85 的自身削弱), 非本数据拟合. model_version=live_poisson_v1 透明可追溯,
留 P2-2 回测调参.

运行(CLI 调试): python -m backend.simulation.live_odds Spain Japan 70 1 0 0 1
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models.dixon_coles import DixonColes  # noqa: E402
from backend.models.match_predictor import WC2026_HOSTS, wc_neutral_host  # noqa: E402
from backend.simulation.mc import EXTRA_TIME_LAMBDA_FRACTION  # noqa: E402

DEFAULT_DC_PARQUET = ROOT / "data" / "processed" / "dixon_coles_current.parquet"
DEFAULT_DC_JSON = ROOT / "data" / "processed" / "dixon_coles_global.json"

DEFAULT_N = 20000
FULL_TIME_MINUTES = 90
# 红牌修正(少一人): 被罚方进攻 ×0.85**n, 对手进攻 ×1.20**n
RED_CARD_OFFENSE_MULT = 0.85
RED_CARD_OPPONENT_MULT = 1.20
MODEL_VERSION = "live_poisson_v1"


class LiveMatchSimulator:
    """赛中实时胜率模拟器(单场, 剩余时间 Poisson + game state 修正).

    用法:
        sim = LiveMatchSimulator("Spain", "Japan")               # 中立场, 从 artifacts 加载 DC
        r = sim.simulate(minute=70, home_score=1, away_score=0,
                         home_reds=0, away_reds=1)
        # → {home_win, draw, away_win, lambda_rem_*, ...}; knockout=True 另含 home_advance
    """

    def __init__(self, home: str, away: str, neutral: bool | None = None,
                 dc=None, hosts: set[str] = WC2026_HOSTS, seed: int | None = None):
        self.home = home
        self.away = away
        # dc=None → 生产从 artifacts 加载; 单测传 synthetic DC(不读 parquet)
        self.dc = dc if dc is not None else DixonColes.from_artifacts(DEFAULT_DC_PARQUET, DEFAULT_DC_JSON)
        self.neutral = neutral if neutral is not None else wc_neutral_host(home, away, hosts)[0]
        self.rng = np.random.default_rng(seed)
        # 赛前 λ_full(与 mc._lambda / WCPredictor 一致: exp(μ)·att/def·exp(γ·¬neutral))
        exp_mu = float(np.exp(self.dc.mu))
        gamma = float(self.dc.gamma)
        g = 0.0 if self.neutral else gamma
        self.lambda_home_full = exp_mu * self.dc.attack[home] / self.dc.defense[away] * np.exp(g)
        self.lambda_away_full = exp_mu * self.dc.attack[away] / self.dc.defense[home]

    def _lambda_remaining(self, minute: int, home_reds: int, away_reds: int) -> tuple[float, float]:
        """剩余时间 λ(分钟缩放 + 红牌修正). minute=90 → frac=0 锁定."""
        frac = max(0.0, (FULL_TIME_MINUTES - minute) / FULL_TIME_MINUTES)
        lam_h = self.lambda_home_full * frac
        lam_a = self.lambda_away_full * frac
        # 红牌: 被罚方进攻削 0.85**n, 对手进攻增 1.20**n
        lam_h *= (RED_CARD_OFFENSE_MULT ** home_reds) * (RED_CARD_OPPONENT_MULT ** away_reds)
        lam_a *= (RED_CARD_OPPONENT_MULT ** home_reds) * (RED_CARD_OFFENSE_MULT ** away_reds)
        return lam_h, lam_a

    def simulate(self, minute: int, home_score: int, away_score: int,
                 home_reds: int = 0, away_reds: int = 0,
                 n: int = DEFAULT_N, knockout: bool = False) -> dict:
        """跑 n 次剩余时间模拟 → 实时胜/平/负(90min 视角); knockout 另算加时点球后胜出.

        minute: 已踢常规分钟(0-90, 超出夹到 90). home/away_score: 当前比分.
        home/away_reds: 红牌数. knockout=True → 另返 home_advance/away_advance(和=1, 无平).
        """
        minute = int(max(0, min(minute, FULL_TIME_MINUTES)))
        lam_h, lam_a = self._lambda_remaining(minute, home_reds, away_reds)
        sampled_h = self.rng.poisson(lam_h, size=n)
        sampled_a = self.rng.poisson(lam_a, size=n)
        total_h = home_score + sampled_h
        total_a = away_score + sampled_a
        home_win = float(np.mean(total_h > total_a))
        draw = float(np.mean(total_h == total_a))
        away_win = float(np.mean(total_h < total_a))
        result = {
            "home": self.home, "away": self.away, "neutral": self.neutral,
            "minute": minute, "home_score": int(home_score), "away_score": int(away_score),
            "home_reds": int(home_reds), "away_reds": int(away_reds),
            "home_win": home_win, "draw": draw, "away_win": away_win,
            "lambda_rem_home": lam_h, "lambda_rem_away": lam_a,
            "model_version": MODEL_VERSION, "n": int(n),
        }
        if knockout:
            result["home_advance"] = self._knockout_advance(
                total_h, total_a, home_reds, away_reds, home_win, draw)
            result["away_advance"] = 1.0 - result["home_advance"]
        return result

    def _knockout_advance(self, total_h: np.ndarray, total_a: np.ndarray,
                          home_reds: int, away_reds: int,
                          home_win_90: float, draw_90: float) -> float:
        """淘汰赛: 90min 平 → 加时(λ_full×1/3 ×红牌) → 仍平 50/50 点球 → 主队胜出概率."""
        if draw_90 == 0.0:
            return home_win_90
        draw_mask = total_h == total_a
        ndraw = int(draw_mask.sum())
        # 加时 λ: 赛前 λ_full × 加时分数 × 红牌修正(加时 30min 独立时段, 不用 frac)
        ot_lam_h = (self.lambda_home_full * EXTRA_TIME_LAMBDA_FRACTION
                    * (RED_CARD_OFFENSE_MULT ** home_reds) * (RED_CARD_OPPONENT_MULT ** away_reds))
        ot_lam_a = (self.lambda_away_full * EXTRA_TIME_LAMBDA_FRACTION
                    * (RED_CARD_OPPONENT_MULT ** home_reds) * (RED_CARD_OFFENSE_MULT ** away_reds))
        ot_h = self.rng.poisson(ot_lam_h, size=ndraw)
        ot_a = self.rng.poisson(ot_lam_a, size=ndraw)
        ot_home_win = ot_h > ot_a
        ot_still_draw = ot_h == ot_a
        # 加时分胜负 → 主胜; 加时仍平 → 50/50 点球
        ot_home_advance = np.where(ot_home_win, True,
                                   np.where(ot_still_draw, self.rng.random(ndraw) < 0.5, False))
        return home_win_90 + draw_90 * float(np.mean(ot_home_advance))


def main(argv: list[str] | None = None) -> None:
    """CLI 调试: python -m backend.simulation.live_odds HOME AWAY [MINUTE H_SCORE A_SCORE H_REDS A_REDS KNOCKOUT]."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("用法: python -m backend.simulation.live_odds HOME AWAY "
              "[MINUTE H_SCORE A_SCORE H_REDS A_REDS KNOCKOUT]")
        print("例:   python -m backend.simulation.live_odds Spain Japan 70 1 0 0 1")
        return
    home, away = argv[0], argv[1]
    minute = int(argv[2]) if len(argv) > 2 else 0
    hs = int(argv[3]) if len(argv) > 3 else 0
    as_ = int(argv[4]) if len(argv) > 4 else 0
    hr = int(argv[5]) if len(argv) > 5 else 0
    ar = int(argv[6]) if len(argv) > 6 else 0
    ko = bool(int(argv[7])) if len(argv) > 7 else False
    sim = LiveMatchSimulator(home, away)
    r = sim.simulate(minute, hs, as_, hr, ar, knockout=ko)
    ko_s = f"  晋级: 主{r['home_advance']:.1%}/客{r['away_advance']:.1%}" if ko else ""
    print(f"{home} v {away}  {minute}' 比分 {hs}-{as_} 红牌 {hr}-{ar}")
    print(f"  实时胜/平/负 = {r['home_win']:.1%} / {r['draw']:.1%} / {r['away_win']:.1%}"
          f"  (λ_rem {r['lambda_rem_home']:.2f}-{r['lambda_rem_away']:.2f}){ko_s}")


if __name__ == "__main__":
    main()

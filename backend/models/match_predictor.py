"""
P0-6 单场预测管线
==================================================
把 DixonColes.predict() 包装成产品就绪的单场预测接口, 并支持从持久化产出加载(无需 refit),
为 P1(Monte Carlo 锦标赛模拟 / FastAPI 端点)提供单场预测原子能力.

三层:
  1. DixonColes.from_artifacts() —— 从 parquet/json 加载模型(dixon_coles.py 内, 此处复用)
  2. 纯函数 top_scores() / expected_goals() / predict_match() —— 整洁产品输出
  3. WCPredictor —— 加载 DC 产出 + 2026 分组, 自动判定中立场/东道主主场

【WC 东道主主场判定】2026 东道主=美/加/墨. WC 场默认中立场; 恰一方为东道主且在本土
作战 → neutral=False(享基础主场 γ). 双东道主相遇 → 两边主场对消 → neutral=True.
依据: P0-4 证 γ_host(东道主"额外"加成)=0, 但基础 γ(本土观众/少旅行)是真的; 数据里东道主
本土场标 neutral=False(见 memory dataset-field-semantics), 与本逻辑一致.
注: γ_host=0 下 host_home/host_away flag 不改 λ(纯记录元信息), 真正影响预测的只有 neutral→γ.

运行 demo: .venv/bin/python backend/models/match_predictor.py Spain Japan
跑测试:   .venv/bin/python -m unittest backend.models.test_match_predictor
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dixon_coles import DixonColes  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DC_PARQUET = ROOT / "data" / "processed" / "dixon_coles_current.parquet"
DEFAULT_DC_JSON = ROOT / "data" / "processed" / "dixon_coles_global.json"
DEFAULT_GROUPS_CSV = ROOT / "data" / "processed" / "worldcup_2026_groups.csv"

WC2026_HOSTS = {"United States", "Canada", "Mexico"}


# ============================================================
# 纯函数层
# ============================================================
def top_scores(score_matrix: np.ndarray, n: int = 5) -> list[tuple[int, int, float]]:
    """从比分矩阵提取 Top-N 最可能比分(已含 τ 修正).

    返回 [(home_goals, away_goals, prob), ...] 按 prob 降序. prob∈[0,1].
    """
    n_goals = score_matrix.shape[0]
    pairs = [(h, a, float(score_matrix[h, a]))
             for h in range(n_goals) for a in range(n_goals)]
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs[:n]


def expected_goals(score_matrix: np.ndarray) -> tuple[float, float]:
    """从比分矩阵反推期望进球(τ 修正后的边际). 返回 (E[home], E[away]).

    比 model 的 λ 更"真实": λ 是 τ 修正前的 Poisson 参数, 这里是修正后分布的实际期望
    (低分格被 τ 挪动后, 期望略有变化). 差异通常很小.
    """
    k = np.arange(score_matrix.shape[0])
    p_home = score_matrix.sum(axis=1)   # P(home=h) = Σ_a score[h,a]
    p_away = score_matrix.sum(axis=0)   # P(away=a) = Σ_h score[h,a]
    return float((k * p_home).sum()), float((k * p_away).sum())


def predict_match(model: DixonColes, home: str, away: str,
                  neutral: bool = True, host_home: bool = False, host_away: bool = False,
                  top_n: int = 5, max_goals: int = 10) -> dict:
    """单场预测 → 整洁产品输出(dict).

    neutral/host_home/host_away: 见 DixonColes.predict(). WC 场用 WCPredictor 自动判定.
    返回字段:
      home/away/neutral/host_home/host_away  输入回显(元信息)
      home_win/draw/away_win                 三概率(home 视角, 和=1, ∈[0,1])
      lambda_home/lambda_away                模型 λ(Poisson 参数, τ 修正前的期望进球)
      expected_home/expected_away            τ 修正后的期望进球(边际)
      top_scores                             [(h,a,prob)...] Top-N 最可能比分
      score_matrix                           完整比分概率矩阵(score[h,a])
    """
    p = model.predict(home, away, neutral=neutral,
                      host_home=host_home, host_away=host_away, max_goals=max_goals)
    eh, ea = expected_goals(p["score_matrix"])
    return {
        "home": home, "away": away,
        "neutral": neutral, "host_home": host_home, "host_away": host_away,
        "home_win": p["home_win"], "draw": p["draw"], "away_win": p["away_win"],
        "lambda_home": p["lambda_home"], "lambda_away": p["lambda_away"],
        "expected_home": eh, "expected_away": ea,
        "top_scores": top_scores(p["score_matrix"], top_n),
        "score_matrix": p["score_matrix"],
    }


def wc_neutral_host(home: str, away: str,
                    hosts: set[str] = WC2026_HOSTS) -> tuple[bool, bool, bool]:
    """判定 WC 单场的 neutral / host_home / host_away.

    规则: 恰一方为东道主(本土作战) → neutral=False(享基础 γ); 都不是或都是 → neutral=True.
    返回 (neutral, host_home, host_away).
    """
    host_home = home in hosts
    host_away = away in hosts
    neutral = not (host_home ^ host_away)   # XOR: 恰一方东道主 → 非中立
    return neutral, host_home, host_away


# ============================================================
# WCPredictor —— 加载产出 + 分组, 产品级单场预测
# ============================================================
class WCPredictor:
    """2026 世界杯单场预测器: 加载 DC 产出 + 分组表, 自动判定中立场/东道主.

    典型用法:
        wp = WCPredictor()                       # 从默认 data/processed/ 加载
        wp.predict("Spain", "Japan")             # 中立场
        wp.predict("United States", "Switzerland")  # 美国本土 → neutral=False 享 γ
    """

    def __init__(self, dc_parquet=DEFAULT_DC_PARQUET, dc_json=DEFAULT_DC_JSON,
                 groups_csv: str | Path = DEFAULT_GROUPS_CSV,
                 hosts: set[str] | None = None) -> None:
        self.model = DixonColes.from_artifacts(dc_parquet, dc_json)
        self.groups = pd.read_csv(groups_csv)
        self.wc_teams = set(self.groups["team"])
        self.hosts = set(hosts) if hosts is not None else set(WC2026_HOSTS)

    def is_host(self, team: str) -> bool:
        return team in self.hosts

    def predict(self, home: str, away: str,
                host_home: bool | None = None, host_away: bool | None = None,
                top_n: int = 5) -> dict:
        """预测一场 WC 比赛. host_home/host_away 传 None → 按东道主身份自动判定."""
        auto_neutral, auto_hh, auto_ha = wc_neutral_host(home, away, self.hosts)
        neutral = auto_neutral
        hh = auto_hh if host_home is None else host_home
        ha = auto_ha if host_away is None else host_away
        # 若调用方显式指定了 host, 则 neutral 由"恰一方 host"重算(覆盖自动判定)
        if host_home is not None or host_away is not None:
            neutral = not (hh ^ ha)
        return predict_match(self.model, home, away, neutral=neutral,
                             host_home=hh, host_away=ha, top_n=top_n)


# ============================================================
# demo: CLI 预测 + WC 抽样对照
# ============================================================
def _fmt_match(r: dict) -> str:
    top = ", ".join(f"{h}-{a}({p:.0%})" for h, a, p in r["top_scores"][:3])
    venue = "中立场" if r["neutral"] else ("本土(享γ)" if (r["host_home"] or r["host_away"]) else "主场")
    return (f"{r['home']} v {r['away']}  [{venue}]\n"
            f"  胜/平/负 = {r['home_win']:.1%} / {r['draw']:.1%} / {r['away_win']:.1%}   "
            f"λ={r['lambda_home']:.2f}-{r['lambda_away']:.2f} (期望 {r['expected_home']:.2f}-{r['expected_away']:.2f})\n"
            f"  最可能比分 Top3: {top}")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    wp = WCPredictor()
    if len(argv) >= 2:
        home, away = argv[0], argv[1]
        print(_fmt_match(wp.predict(home, away)))
        return
    # 无参数 → WC 抽样对照(中立场 + 东道主本土)
    print("=" * 64)
    print("P0-6 单场预测 demo(从持久化 DC 产出加载, 无需 refit)")
    print("=" * 64)
    samples = [
        ("Spain", "Japan"),                 # 中立场
        ("Argentina", "France"),            # 中立场 强强
        ("United States", "Switzerland"),   # 美国东道主本土 → 享 γ
        ("Mexico", "Ecuador"),              # 墨西哥东道主本土 → 享 γ
    ]
    for home, away in samples:
        if home not in wp.wc_teams or away not in wp.model.attack:
            print(f"  (跳过 {home} v {away}: 不在参赛/历史数据)")
            continue
        print("-" * 64)
        print(_fmt_match(wp.predict(home, away)))
    # 东道主本土 vs 纯中立 对照(美国)
    print("-" * 64)
    p_neu = wp.predict("United States", "Switzerland", host_home=False, host_away=False)
    p_home = wp.predict("United States", "Switzerland")  # 自动本土
    print(f"美国 v 瑞士: 纯中立胜率 {p_neu['home_win']:.1%} → 本土(享γ) {p_home['home_win']:.1%}  "
          f"Δ=+{(p_home['home_win']-p_neu['home_win'])*100:.1f}pp")
    print("=" * 64)


if __name__ == "__main__":
    main()

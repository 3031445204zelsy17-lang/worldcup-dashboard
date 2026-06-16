"""
P0-4 Dixon-Coles 单场比分模型
=============================================================
方法论: Dixon & Coles (1997) —— 对独立 Poisson 的低分修正模型
输入: data/processed/international_history.parquet  (P0-2 产出)
产出: data/processed/dixon_coles_current.parquet    (各队 attack/defense 快照)

核心模型
--------
单场两队期望进球(对数线性):
    ln λ_home = μ + att_home − def_away + γ·I(普通主场) + γ_host·I(东道主)
    ln λ_away = μ + att_away − def_home
  att  大 = 进攻强;  def 大 = 防守强(直觉一致, 用减号)
  μ        基准进球数(对数)
  γ        普通主场优势(从 neutral=False 的主客场赛拟合, 样本巨大)
  γ_host   东道主加成(锦标赛中立场里 home==country 的东道主享额外加成)
  ρ        低分修正系数(Dixon-Coles 专属)

低分修正 τ(ρ): 独立 Poisson 会系统性高估 1-0/0-1、低估 0-0/1-1, τ 只动这 4 格:
    (0,0): ×(1 − λ_h·λ_a·ρ)
    (0,1): ×(1 + λ_h·ρ)
    (1,0): ×(1 + λ_a·ρ)
    (1,1): ×(1 − ρ)
    其余 : ×1
  ρ<0 的效果 = 增大 0-0/1-1 概率, 减小 1-0/0-1 (修正独立 Poisson 的低分偏差)
  典型拟合值 ρ ≈ −0.1.

时间衰减 φ(t): 越近的比赛权重越高, 老"古"比赛自动贬值
    φ = exp(−ξ·Δt),  ξ = ln2 / half_life_days
  半衰期默认 730 天(2 年): 2 年前的比赛权重只剩一半.
  → 全量历史可用, 不必硬切窗; 近期性由 φ 保证(与 Elo 的滚动更新机制正交).

设计说明
--------
1. 【与 Elo 的分工】Elo 给"实力标量"(谁强), Dixon-Coles 给"攻防二维 + 比分分布"
   (强多少、踢成几比几). 两者不重叠: Elo 靠滚动更新实现近期性, DC 靠 φ(t) 显式
   衰减. 本层不复加高原 H(已由 Elo 层 home_advantage_for 处理), 不双重计权.
2. 【东道主加成 ⭐】世界杯是中立场, 普通 γ=0; 但东道主(2026=美/加/墨)在自己国家
   作战仍享真实优势(全国助威/赛程/裁判心理). 训练数据里:
     host_match = (neutral=True 且 home_team == country)   ← 锦标赛东道主
   普通主场(neutral=False)用 γ, 东道主锦标赛场用 γ_host, 二者独立不重叠(无双重计).
   三国联办的赛区差异(美国赛区获益最大/墨西哥城高原)留 Phase 1 精修, P0 用统一 γ_host,
   回测(P0-7)开关对比后定夺是否进 Phase 1.
3. 【参数可识别】attack 全队均值归一(Σ att_log = 0), 让 μ 吸收基准; defense 自由.
   归一在 nll 内部用 reparametrization(att_log − att_log.mean())实现, 无需约束优化,
   L-BFGS-B 无约束跑得快且稳.
4. 【防数据泄露】fit(df, as_of=...) 只用 date < as_of 的比赛, 且 φ 以 as_of 为基准衰减
   → 时点快照, 直接支撑 P0-7 walk-forward(测试集比赛不污染训练).
5. 【中立场预测】predict 默认 neutral=True, host_home/host_away 按需开 → 世界杯场景.
6. 【ρ 数值稳定】bounds ∈ [−0.2, 0.2], 并对 τ 的乘子 clip 到 >0 防 log(负).

依赖: numpy / scipy / pandas  (CLAUDE.md 技术栈)
运行自检: .venv/bin/python backend/models/dixon_coles.py
跑测试:   .venv/bin/python -m unittest backend.models.test_dixon_coles
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Union

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

# ============================================================
# 常量 / 默认
# ============================================================
DEFAULT_HALF_LIFE_DAYS = 730.0   # 时间衰减半衰期(2 年); 国际队阵容变化快
MAX_GOALS = 10                   # 预测比分网格上界 0..10
LN2 = np.log(2.0)

# 优化器参数边界(log 空间). L-BFGS-B 用.
BOUNDS = {
    "mu": (-2.0, 2.0),        # 基准进球对数, exp≈0.14~7.4
    "gamma": (0.0, 1.0),      # 普通主场优势(非负, log scale)
    "gamma_host": (0.0, 1.0), # 东道主加成(非负)
    "rho": (-0.2, 0.2),       # 低分修正(典型 ±0.1)
    "log_param": (-3.0, 3.0), # 每队 attack/defense 的 log
}

DateLike = Union[date, datetime, str, pd.Timestamp]


# ============================================================
# 纯函数层
# ============================================================
def time_decay_weights(dates: pd.Series, as_of: DateLike, half_life_days: float) -> np.ndarray:
    """时间衰减权重 φ = exp(−ξ·Δt), ξ = ln2 / half_life_days.

    越近权重越高; 半衰期处权重=0.5. Δt 取 max(0, as_of − date) 天.
    """
    xi = LN2 / half_life_days
    delta_days = (pd.Timestamp(as_of) - pd.to_datetime(dates)).dt.total_seconds().to_numpy() / 86400.0
    delta_days = np.clip(delta_days, 0.0, None)
    return np.exp(-xi * delta_days)


def tau_correction(h: np.ndarray, a: np.ndarray,
                   lam_h: np.ndarray, lam_a: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles 低分修正因子 τ(向量化).

    只在 (0,0)(0,1)(1,0)(1,1) 四种比分非 1. ρ<0 → 增大 0-0/1-1、减小 1-0/0-1.
    返回 τ 数组(其余位置为 1.0). 对乘子 clip 下界 1e-12 防 log(0/负).
    """
    tau = np.ones_like(lam_h)
    m00 = (h == 0) & (a == 0)
    m01 = (h == 0) & (a == 1)
    m10 = (h == 1) & (a == 0)
    m11 = (h == 1) & (a == 1)
    tau[m00] = 1.0 - lam_h[m00] * lam_a[m00] * rho
    tau[m01] = 1.0 + lam_h[m01] * rho
    tau[m10] = 1.0 + lam_a[m10] * rho
    tau[m11] = 1.0 - rho
    return np.clip(tau, 1e-12, None)


def _ln_factorial_goals(max_goals: int) -> np.ndarray:
    """预算 ln(k!) for k=0..max_goals, 给 Poisson 归一项用."""
    k = np.arange(max_goals + 1)
    return gammaln(k + 1.0)


# ============================================================
# DixonColes —— 有状态地拟合攻防参数, 支持时点快照与单场预测
# ============================================================
class DixonColes:
    """Dixon-Coles 攻防参数估计 + 单场比分预测.

    典型用法:
        m = DixonColes().fit(df)                 # 全量历史 + 2 年半衰减
        m.predict("Spain", "Japan")              # 中立场单场比分分布
        m.fit(df, as_of="2022-01-01")            # 时点快照(防泄露)
    """

    def __init__(self, half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
                 host_bonus: float | None = None) -> None:
        """
        half_life_days: 时间衰减半衰期(天). 默认 730(2 年).
        host_bonus:     None=从数据拟合 γ_host; 给定 float=固定该值不拟合(回测对照用).
        """
        self.half_life_days = half_life_days
        self.host_bonus = host_bonus  # None 表示拟合

        # 拟合结果
        self.mu: float = 0.0
        self.gamma: float = 0.0
        self.gamma_host: float = host_bonus if host_bonus is not None else 0.0
        self.rho: float = 0.0
        self.attack: dict[str, float] = {}    # exp 化后的进攻参数(>0, 均值≈1)
        self.defense: dict[str, float] = {}   # exp 化后的防守参数(>0, 均值≈1)
        self.teams: list[str] = []
        self._fitted = False

    # ---------- 内部: 把 DataFrame 折成 numpy 数组 ----------
    def _prepare(self, df: pd.DataFrame, as_of: DateLike | None):
        """提取向量化字段 + 时间权重 + 队索引. 返回拟合所需的全部数组."""
        if as_of is not None:
            df = df[df["date"] < pd.Timestamp(as_of)].copy()
        df = df.sort_values("date")

        h = df["home_score"].to_numpy(dtype=float)
        a = df["away_score"].to_numpy(dtype=float)
        neutral = df["neutral"].to_numpy(dtype=bool)
        home = df["home_team"].to_numpy()
        away = df["away_team"].to_numpy()
        country = df["country"].to_numpy()

        # 队全集(按出现顺序稳定)
        teams = sorted(set(home.tolist()) | set(away.tolist()))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        ih = np.array([idx[t] for t in home], dtype=int)
        ia = np.array([idx[t] for t in away], dtype=int)
        is_wc = (df["tournament"].to_numpy() == "FIFA World Cup")
        home_flag = (~neutral).astype(float)  # 普通主场 = 非中立场(本国作战)
        # 世界杯东道主: martj42 数据里东道主本国作战场标 neutral=False(country==东道主),
        # 享普通 γ 之外再叠加 γ_host(全国助威/赛程的"锦标赛级"额外加成).
        host_flag = ((~neutral) & is_wc & (home == country)).astype(float)

        w = time_decay_weights(df["date"], as_of if as_of is not None else df["date"].max(),
                               self.half_life_days)

        return dict(h=h, a=a, ih=ih, ia=ia, n=n, teams=teams,
                    home_flag=home_flag, host_flag=host_flag, w=w)

    def _neg_log_likelihood(self, params: np.ndarray, P: dict) -> float:
        """加权负对数似然(NLL). params 布局见 fit()."""
        n = P["n"]
        mu = params[0]
        gamma = params[1]
        gamma_host = params[2]
        rho = params[3]
        att_log = params[4:4 + n]
        def_log = params[4 + n:4 + 2 * n]

        # attack 全队均值归一 → μ 可识别(设计说明第 3 条)
        att_log = att_log - att_log.mean()

        lam_h = np.exp(mu + att_log[P["ih"]] - def_log[P["ia"]] + gamma * P["home_flag"] + gamma_host * P["host_flag"])
        lam_a = np.exp(mu + att_log[P["ia"]] - def_log[P["ih"]])

        # τ 修正因子(对数)
        tau = tau_correction(P["h"], P["a"], lam_h, lam_a, rho)
        log_tau = np.log(tau)

        # Poisson 对数似然(手算, 比 poisson.logpmf 快): h·ln λ − λ − ln h!
        ln_fact = gammaln(P["h"] + 1.0) + gammaln(P["a"] + 1.0)
        ll = log_tau + P["h"] * np.log(lam_h) - lam_h + P["a"] * np.log(lam_a) - lam_a - ln_fact

        return -np.sum(P["w"] * ll)

    # ---------- 公开 API ----------
    def fit(self, df: pd.DataFrame, as_of: DateLike | None = None) -> "DixonColes":
        """加权 MLE 拟合. 需含列:
        date / home_team / away_team / home_score / away_score / neutral / country.

        as_of: 只用 date < as_of 的比赛, 且 φ 以 as_of 为基准衰减(防泄露). None=用全量.
        """
        P = self._prepare(df, as_of)
        n = P["n"]
        self.teams = P["teams"]

        # 初始值: μ=ln(均进球), attack/defense=0(log, 即 exp=1), γ 微正, ρ 微负
        avg_goals = float(np.mean(np.concatenate([P["h"], P["a"]])))
        x0 = np.concatenate([
            [np.log(max(avg_goals, 0.3))],   # mu
            [0.25],                           # gamma ≈ ln(1.28)
            [self.gamma_host],                # gamma_host (初值)
            [-0.05],                          # rho
            np.zeros(n),                      # attack (log)
            np.zeros(n),                      # defense (log)
        ])

        bounds = ([BOUNDS["mu"]] + [BOUNDS["gamma"]] +
                  [BOUNDS["gamma_host"]] + [BOUNDS["rho"]] +
                  [BOUNDS["log_param"]] * n + [BOUNDS["log_param"]] * n)

        # host_bonus 固定时, 把 gamma_host 的 bound 钉死在该值(不优化)
        fix_host = self.host_bonus is not None
        if fix_host:
            bounds[2] = (self.host_bonus, self.host_bonus)

        res = minimize(self._neg_log_likelihood, x0, args=(P,), method="L-BFGS-B",
                       bounds=bounds, options={"maxiter": 500})

        # 解包参数
        self.mu = float(res.x[0])
        self.gamma = float(res.x[1])
        self.gamma_host = float(res.x[2])
        self.rho = float(res.x[3])
        att_log = res.x[4:4 + n] - res.x[4:4 + n].mean()   # 归一
        def_log = res.x[4 + n:4 + 2 * n]
        self.attack = {t: float(np.exp(al)) for t, al in zip(self.teams, att_log)}
        self.defense = {t: float(np.exp(dl)) for t, dl in zip(self.teams, def_log)}
        self._fitted = True
        self._res = res
        return self

    def fit_at(self, df: pd.DataFrame, as_of: DateLike) -> "DixonColes":
        """as_of 时点快照: 只用 date < as_of 的比赛拟合(防泄露, 供 P0-7 walk-forward)."""
        return self.fit(df, as_of=as_of)

    def predict(self, home: str, away: str,
                neutral: bool = True, host_home: bool = False, host_away: bool = False,
                max_goals: int = MAX_GOALS) -> dict:
        """单场比分分布预测.

        neutral:    True=中立场(世界杯, γ=0); False=普通主场(home 享 γ).
        host_home:  home 队是否为该场东道主(享 γ_host). 世界杯预测时给东道主 True.
        host_away:  away 队是否为东道主(罕见, 双东道主相遇场景).
        返回: lambda_home/away、score_matrix、home_win/draw/away_win.
        """
        if not self._fitted:
            raise RuntimeError("先 fit() 再 predict().")
        # 未见过的队按全队均值(attack=1, defense=1)处理
        att_h = self.attack.get(home, 1.0)
        att_a = self.attack.get(away, 1.0)
        def_h = self.defense.get(home, 1.0)
        def_a = self.defense.get(away, 1.0)

        g_home = 0.0 if neutral else self.gamma
        lam_h = np.exp(self.mu) * att_h * (1.0 / def_a) * np.exp(g_home + (self.gamma_host if host_home else 0.0))
        lam_a = np.exp(self.mu) * att_a * (1.0 / def_h) * np.exp(self.gamma_host if host_away else 0.0)

        # 独立 Poisson 比分网格
        k = np.arange(max_goals + 1)
        log_fact = _ln_factorial_goals(max_goals)
        ph = np.exp(k * np.log(lam_h) - lam_h - log_fact)
        pa = np.exp(k * np.log(lam_a) - lam_a - log_fact)
        score = np.outer(ph, pa)  # score[h, a]

        # τ 低分修正
        H, A = np.meshgrid(k, k, indexing="ij")
        tau = tau_correction(H.ravel(), A.ravel(),
                             np.full(H.size, lam_h), np.full(A.size, lam_a), self.rho).reshape(H.shape)
        score = score * tau
        score = score / score.sum()   # 归一(τ 改变了总和)

        home_win = float(np.tril(score, -1).sum())   # 下三角 h>a
        draw = float(np.trace(score))                 # 对角 h==a
        away_win = float(np.triu(score, 1).sum())     # 上三角 h<a

        return {
            "lambda_home": float(lam_h),
            "lambda_away": float(lam_a),
            "score_matrix": score,
            "home_win": home_win,
            "draw": draw,
            "away_win": away_win,
        }

    def to_frame(self) -> pd.DataFrame:
        """各队 attack/defense → DataFrame(降序), 便于存 parquet / 展示."""
        if not self._fitted:
            raise RuntimeError("先 fit() 再 to_frame().")
        net = {t: self.attack[t] / self.defense[t] for t in self.teams}
        frame = pd.DataFrame(
            [{"team": t, "attack": self.attack[t], "defense": self.defense[t],
              "net_strength": net[t]} for t in self.teams],
            columns=["team", "attack", "defense", "net_strength"],
        )
        return frame.sort_values("net_strength", ascending=False).reset_index(drop=True)

    def global_params(self) -> dict:
        """全局参数(μ/γ/γ_host/ρ/half_life) → dict, 供序列化与回测记录."""
        return {
            "mu": self.mu,
            "gamma": self.gamma,
            "gamma_host": self.gamma_host,
            "rho": self.rho,
            "half_life_days": self.half_life_days,
            "host_fixed": self.host_bonus is not None,
        }


# ============================================================
# 自检: 全量历史拟合 → 存 parquet + 打印全局参数与东道主对照
# ============================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    HIST = ROOT / "data" / "processed" / "international_history.parquet"
    OUT_PARQUET = ROOT / "data" / "processed" / "dixon_coles_current.parquet"
    OUT_JSON = ROOT / "data" / "processed" / "dixon_coles_global.json"

    if not HIST.exists():
        sys.exit(f"[ERR] 找不到 {HIST}，请先跑 P0-2 (backend/data/load_results.py)")

    df = pd.read_parquet(HIST)
    model = DixonColes().fit(df)

    frame = model.to_frame()
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT_PARQUET, index=False)
    gp = model.global_params()
    with open(OUT_JSON, "w") as f:
        json.dump(gp, f, indent=2)

    print("=" * 64)
    print(f"P0-4 Dixon-Coles 拟合完成 | {len(df)} 场 → {len(frame)} 队")
    print(f"全局参数: μ={gp['mu']:.3f}  γ={gp['gamma']:.3f}  "
          f"γ_host={gp['gamma_host']:.3f}  ρ={gp['rho']:.3f}  "
          f"(half_life={gp['half_life_days']:.0f}d)")
    print(f"  解读: 普通主场进球×{np.exp(gp['gamma']):.2f}  "
          f"东道主进球×{np.exp(gp['gamma_host']):.2f}  "
          f"低分修正 ρ{'<' if gp['rho']<0 else '>'}0 "
          f"({'符合预期' if gp['rho'] < 0 else '⚠️ 与典型相反, 回测留意'})")
    print("=" * 64)
    # net_strength 排名会被 ConIFA 类小队(Tibet/East Turkestan/Chagos Islands…)的小样本参数
    # 污染: 这些非正式队彼此间踢低级别比赛 → attack/defense 拟合自由 → 极端值.
    # 正式队之间的相对参数仍正确(μ 补偿了归一), 但 net 这个排名指标对小队失真.
    # 收缩修复是 P0-5 的活; 这里只看主流国家队(对齐"聚焦参赛 48 队"):
    showcase = ["Spain", "Argentina", "France", "England", "Brazil", "Germany",
                "Netherlands", "Portugal", "Belgium", "Italy",
                "United States", "Canada", "Mexico"]
    sc = frame[frame["team"].isin(showcase)].copy()
    order = {t: i for i, t in enumerate(showcase)}
    sc = sc.assign(_o=sc["team"].map(order)).sort_values("_o")[["team", "attack", "defense", "net_strength"]]
    print("主流国家队 attack/defense (net=attack/defense):")
    print(sc.to_string(index=False))
    print("-" * 64)

    # 2026 三东道主: 纯中立场 vs 东道主主场(本国作战+锦标赛加成) 对照, 对手取中游 Switzerland
    hosts = ["United States", "Canada", "Mexico"]
    opp = "Switzerland"
    print(f"2026 东道主加成对照 (对手 {opp}):")
    for h in hosts:
        if h not in model.attack:
            print(f"  {h}: 不在历史数据(跳过)")
            continue
        p_neu = model.predict(h, opp, neutral=True, host_home=False)   # 纯中立(基准)
        p_host = model.predict(h, opp, neutral=False, host_home=True)   # 东道主主场: γ + γ_host
        print(f"  {h:14s} 纯中立 胜率={p_neu['home_win']:5.1%} (λ={p_neu['lambda_home']:.2f})"
              f"  →  东道主主场 胜率={p_host['home_win']:5.1%} (λ={p_host['lambda_home']:.2f})"
              f"  Δ=+{(p_host['home_win']-p_neu['home_win'])*100:4.1f}pp")
    print("-" * 64)
    print(f"已存: {OUT_PARQUET.name} + {OUT_JSON.name}")
    print("=" * 64)

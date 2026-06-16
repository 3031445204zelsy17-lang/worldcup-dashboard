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
2. 【东道主加成已砍 ⚠️】曾设 γ_host 给世界杯东道主额外加成, 2026 前向验证证伪:
   ① 赛前样本(排除 2026)拟合 γ_host=0——历史东道主虽有 121 场/61% 胜率, 但时间衰减
      (半衰期 2 年)把老样本杀光(有效权重仅 1.3), 只剩近 4 届(含卡塔尔 2022 小组全败)
      信号被稀释;
   ② 东道主优势已被 γ(东道主场 neutral=False 享 γ)+ 队参数(主场友谊赛/预选赛水分)双重
      吸收, γ_host 无独立信号; 强行拟合要么=0, 要么靠目标数据泄露假非零(全量 fit 的 0.098
      就是偷看 2026 东道主 2 胜 1 平反推).
   → γ_host 默认固定 0(host_bonus 参数), 东道主优势改由 γ+队参数两条干净渠道体现.
     队参数主场水分的根治在 P0-5 收缩(Elo 先验). 赛区级加成(美国赛区/墨西哥城高原交叉)
     留 Phase 1. predict 保留 host_home/host_away 接口但默认无作用(γ_host=0), 备 Phase 1.
3. 【参数可识别】attack 全队均值归一(Σ att_log = 0), 让 μ 吸收基准; defense 自由.
   归一在 nll 内部用 reparametrization(att_log − att_log.mean())实现, 无需约束优化,
   L-BFGS-B 无约束跑得快且稳.
4. 【防数据泄露】fit(df, as_of=...) 只用 date < as_of 的比赛, 且 φ 以 as_of 为基准衰减
   → 时点快照, 直接支撑 P0-7 walk-forward(测试集比赛不污染训练).
5. 【中立场预测】predict 默认 neutral=True, host_home/host_away 按需开 → 世界杯场景.
6. 【ρ 数值稳定】bounds ∈ [−0.2, 0.2], 并对 τ 的乘子 clip 到 >0 防 log(负).
7. 【P0-5 含金量加权 + Elo 先验收缩】两机制治 P0-4 三个已知病:
   ① 赛事含金量权重 w(importance_weight): 友谊赛/邀请杯 0.25, Olympic U-23/综合运动会/CONIFA
      剔除(0); 喂进似然逐场权重(×φ)和有效样本量 n_eff(team)=Σw·φ.
   ② Elo 先验 post-hoc 收缩: 稀疏队(n_eff 小, 含 ConIFA 小队/Canada 类友谊赛水分队)的
      att/def 拉向 Elo 隐含的实力先验 s=β·(Elo−mean); 正式队(n_eff 大)几乎不动.
      θ_shrunk = (n_eff·θ_mle + κ·s)/(n_eff+κ). 收缩后重中心化 → 平均进球率不变(只去单队污染).
   ③ 收缩启用条件: fit(elo=...) 传 Elo 且 κ>0. Olympic=0 因 U-23 阵容对成年实力系统性误导
      (阵容 lineup 数据 P0 没有, 但赛事名已编码"非A队": Olympic/*Games/CONIFA). κ/档位调参留 P0-7.
   ⚠️ 生产默认【不收缩】: 12 场前向验证收缩未见显著收益(含金量加权已独立达成方法论改进),
      且 48 支 WC 队数据都充足(n_eff 15-25)、DC 无对阵项 → 收缩对 WC 产品近于 no-op(仅去污染
      不参赛的 ConIFA 排名). 故生产 fit() 不传 elo(纯MLE); 收缩作为 safety-net 实现并测试,
      传 elo 即开. P0-7 的 128 场回测做收缩开/关对照, 一锤定音是否默认启用.

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
DEFAULT_SHRINKAGE_KAPPA = 5.0    # P0-5 Elo 先验收缩强度(先验等效比赛数). 5: n_eff=5→50%先验,
                                 # n_eff=20 的强队仅 20%先验, n_eff≈0 的稀疏队纯走先验. 12 场前向
                                 # 验证区分不了 κ(噪声), 精调(含准确率)留 P0-7 的 128 场回测.
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

# ============================================================
# 赛事重要性分档 (P0-5) —— 含金量喂进似然权重 + 收缩用有效样本量
# ============================================================
# 设计动机: 友谊赛/邀请杯水分大(37% 场次且 85% 带主场 → 主场友谊赛灌水队参数,
# Canada 类过度自信); Olympic U-23 / 综合运动会 / 非FIFA业余队阵容系统性非A队,
# 拿它们的赛果拟成年国家队攻防是污染. 含金量按赛事类型分档:
#   1.00 世界杯正赛 | 0.90 洲际大赛正赛 | 0.75 预选赛 | 0.50 次级正式(国家联赛+主要区域杯)
#   0.25 友谊/邀请杯 | 0.00 业余/非成年(剔除: Olympic U-23 / *Games / CONIFA / Viva / Island Games)
# 注: 阵容(lineup)数据 P0 没有, 但赛事名本身编码了"非A队"信号(Olympic=U-23, *Games=综合运动会,
# CONIFA=非FIFA业余), 光靠类型降权就能抓到一大半"阵容非主力". 真 lineup 钩子留 Phase 1.
CONTINENTAL_FINALS = {                 # 洲际大赛正赛(决赛圈, 全主力最高赌注之一)
    "UEFA Euro", "Copa América", "African Cup of Nations", "AFC Asian Cup",
    "Gold Cup", "Oceania Nations Cup", "CONCACAF Championship", "Confederations Cup",
}
MAJOR_REGIONAL = {                     # 次级正式: 国家联赛 + 主要区域联邦杯(全主力, 层级低于洲际正赛)
    "UEFA Nations League", "CONCACAF Nations League",
    "Gulf Cup", "AFF Championship", "ASEAN Championship", "EAFF Championship",
    "SAFF Cup", "WAFF Championship", "CAFA Nations Cup",
    "CECAFA Cup", "COSAFA Cup", "UNCAF Cup", "CFU Caribbean Cup",
    "Amílcar Cabral Cup", "West African Cup", "UDEAC Cup", "Palestine Cup",
    "Arab Cup", "AFC Challenge Cup",
}
_AMATEUR_MARKERS = ("CONIFA", "Viva", "FIFI", "ELF")   # 非FIFA业余队赛事名标记

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


def importance_weight(tournament: str) -> float:
    """赛事类型 → 重要性权重 w ∈ [0, 1]  (P0-5 含金量分档).

    返回值:
      1.00  FIFA World Cup                       世界杯正赛
      0.90  洲际大赛正赛                          Euro/Copa/AFCON/Asian Cup/Gold Cup/Confederations
      0.75  预选赛 (高赌注全主力)                  所有 *qualification
      0.50  次级正式 (国家联赛 + 主要区域联邦杯)     Nations League / Gulf / AFF / EAFF / CECAFA / ...
      0.25  友谊 / 邀请杯                          Friendly / FIFA Series / 其余有名邀请杯(兜底)
      0.00  业余 / 非成年 (剔除)                    Olympic Games(U-23) / *Games(综合运动会) /
                                                 CONIFA / Viva / Island Games / Muratti Vase

    w 喂两处: ① 似然逐场权重(乘时间衰减 φ 进 _neg_log_likelihood);
             ② 收缩用的有效样本量 n_eff(team) = Σ_{该队的场} w·φ.

    排序要点: 业余/非成年先判(0), 防 "CONIFA *qualification" 被当预选赛(0.75).
    Olympic=0 因 U-23 阵容对成年实力系统性误导(赛事名即"非A队"标记, 无需 lineup 数据).
    """
    t = tournament
    # —— 业余/非成年 → 剔除 (先判, 兜住 CONIFA *qualification 之类) ——
    if any(m in t for m in _AMATEUR_MARKERS):
        return 0.00
    if t == "Olympic Games" or "Games" in t:        # U-23 / 综合运动会(常年龄限制)
        return 0.00
    if t in {"Island Games", "Muratti Vase"}:       # 业余微型赛事兜底
        return 0.00
    # —— 正式赛(按含金量降档) ——
    if t == "FIFA World Cup":
        return 1.00
    if t in CONTINENTAL_FINALS:
        return 0.90
    if "qualification" in t.lower():
        return 0.75
    if t in MAJOR_REGIONAL:
        return 0.50
    # —— 兜底: Friendly / FIFA Series / 其余有名邀请杯(King's Cup/Kirin/Merdeka...) ——
    return 0.25


def _elo_prior_shrink(
    teams: list[str],
    ih: np.ndarray, ia: np.ndarray,
    match_w: np.ndarray,
    att_log: np.ndarray, def_log: np.ndarray,
    elo: dict, kappa: float,
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """Elo 先验 post-hoc 收缩  (P0-5 核心).

    稀疏队(n_eff 小)的 attack/defense 被拉向 Elo 隐含的实力先验; 数据充足(n_eff 大)的队几乎不动.
    数学:
      n_eff(i) = Σ_{i 参加的场} match_w        (match_w = 含金量 w · 时间衰减 φ)
      Elo → log 强度先验: 观测综合实力 z=(att+def_c)/2 对 Elo 偏离过原点回归 → 斜率 β, 先验 s=β·(Elo−mean)
      收缩: att_shr = [n_eff·att + κ·s]/(n_eff+κ),  def 同理(中心化空间)
    重中心化保证全局校准不变(平均进球率 μ 不漂): attack 归 0 均值, defense 保原基线 def_mean.
    返回 (att_shrunk[mean0], def_shrunk[def_mean], beta, neff).
    """
    n = len(teams)
    neff = np.zeros(n)
    np.add.at(neff, ih, match_w)
    np.add.at(neff, ia, match_w)

    # κ≤0 = 无收缩 → 直接返回 MLE(归一). 跳过后续, 也避免 neff=0 队的 0/0 NaN 污染.
    if kappa <= 0:
        return att_log - att_log.mean(), def_log, 0.0, neff

    elo_arr = np.array([elo.get(t, 1500.0) for t in teams], dtype=float)
    e = elo_arr - elo_arr.mean()                       # Elo 偏离(全队均值中心化)
    def_mean = float(def_log.mean())                   # defense 全局基线(与 μ 非识别, 收缩后须保)
    def_c = def_log - def_mean
    z = 0.5 * (att_log + def_c)                        # 观测综合实力(att 已 mean0, def 中心化)
    beta = float(np.sum(z * e) / (np.sum(e * e) + 1e-12))
    s = beta * e                                        # Elo 先验(mean0): 强队 att/def 同向都高

    wd = neff / (neff + kappa)                          # 数据权重: n_eff 大→1(信数据), 小→0(信先验)
    att_shr = wd * att_log + (1.0 - wd) * s
    def_shr_c = wd * def_c + (1.0 - wd) * s
    # 重中心化 → 平均 λ 严格不变(μ + mean(att)=0 − mean(def)=def_mean 两边相等)
    att_shr = att_shr - att_shr.mean()
    def_shr = (def_shr_c - def_shr_c.mean()) + def_mean
    return att_shr, def_shr, beta, neff


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
                 host_bonus: float = 0.0,
                 use_importance_weight: bool = True,
                 shrinkage_kappa: float = DEFAULT_SHRINKAGE_KAPPA) -> None:
        """
        half_life_days:        时间衰减半衰期(天). 默认 730(2 年).
        host_bonus:            东道主额外加成(固定值, 默认 0=不加成). 经前向验证证伪不再拟合
                               (详见模块 docstring 设计说明第 2 条). 传非 0 可做敏感性试验.
        use_importance_weight: P0-5 含金量加权(默认开). 关掉则所有赛事 imp=1(退回纯 P0-4, 供消融对比).
        shrinkage_kappa:       P0-5 Elo 先验收缩强度(先验的"等效比赛数"伪计数). 默认 5:
                               n_eff=5 的队收缩 50%, n_eff=20 的强队仅 20%, n_eff≈0 的稀疏队纯走先验.
                               12 场前向验证区分不了 κ(噪声), 精调留 P0-7 的 128 场回测.
                               传 elo=None 或 kappa=0 即不收缩(纯 MLE).
        """
        self.half_life_days = half_life_days
        self.host_bonus = host_bonus
        self.use_importance_weight = use_importance_weight
        self.shrinkage_kappa = shrinkage_kappa

        # 拟合结果
        self.mu: float = 0.0
        self.gamma: float = 0.0
        self.gamma_host: float = host_bonus
        self.rho: float = 0.0
        self.attack: dict[str, float] = {}    # exp 化后的进攻参数(>0, 均值≈1)
        self.defense: dict[str, float] = {}   # exp 化后的防守参数(>0, 均值≈1)
        self.teams: list[str] = []
        # P0-5 收缩相关(未收缩时 neff 仍填, shrink_applied=False)
        self.neff: dict[str, float] = {}      # 各队有效样本量 Σ w·φ
        self.shrink_beta: float | None = None # Elo→log强度回归斜率(收缩启用时)
        self.shrink_applied: bool = False
        self._fitted = False
        self._P: dict | None = None           # 最近一次 _prepare 的数组(ih/ia/w…), 供 shrunk_variant 复用

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
        # P0-5 含金量加权: imp ∈ [0,1] 按赛事类型(友谊赛/邀请杯/Olympic U-23...降权).
        # 关掉则 imp=1(纯 P0-4). 组合权重 w_eff = φ(近期) × imp(含金量) 同时喂似然与 n_eff.
        if self.use_importance_weight:
            imp = np.array([importance_weight(t) for t in df["tournament"]], dtype=float)
        else:
            imp = np.ones(len(df), dtype=float)
        w_eff = w * imp

        return dict(h=h, a=a, ih=ih, ia=ia, n=n, teams=teams,
                    home_flag=home_flag, host_flag=host_flag, w=w_eff)

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
    def fit(self, df: pd.DataFrame, as_of: DateLike | None = None,
            elo: dict | None = None) -> "DixonColes":
        """加权 MLE 拟合 + (可选)Elo 先验收缩. 需含列:
        date / home_team / away_team / home_score / away_score / tournament / neutral / country.

        as_of: 只用 date < as_of 的比赛, 且 φ 以 as_of 为基准衰减(防泄露). None=用全量.
        elo:   {team: rating}. 给了且 shrinkage_kappa>0 → 跑 P0-5 Elo 先验收缩(稀疏队回先验,
               正式队几乎不动). None → 纯 MLE(向后兼容, 含金量加权仍生效).
        """
        P = self._prepare(df, as_of)
        n = P["n"]
        self.teams = P["teams"]
        self._P = P   # 供 shrunk_variant 复用 ih/ia/w(同一 MLE 派生多 κ, P0-7)

        # 有效样本量 n_eff(team) = Σ_{该队的场} w_eff —— 始终计算(收缩强度指标, 也供 to_frame)
        neff = np.zeros(n)
        np.add.at(neff, P["ih"], P["w"])
        np.add.at(neff, P["ia"], P["w"])
        self.neff = {t: float(neff[i]) for i, t in enumerate(self.teams)}

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

        # γ_host 不再拟合(前向验证证伪): 钉死 bound = host_bonus(默认 0). 赛区级加成留 Phase 1.
        bounds[2] = (self.host_bonus, self.host_bonus)

        res = minimize(self._neg_log_likelihood, x0, args=(P,), method="L-BFGS-B",
                       bounds=bounds, options={"maxiter": 500})

        # 解包参数(无约束 MLE 结果)
        self.mu = float(res.x[0])
        self.gamma = float(res.x[1])
        self.gamma_host = float(res.x[2])
        self.rho = float(res.x[3])
        att_log = res.x[4:4 + n] - res.x[4:4 + n].mean()   # attack 归一(mean0)
        def_log = res.x[4 + n:4 + 2 * n]

        # P0-5 Elo 先验 post-hoc 收缩: 给了 elo 且 κ>0 才做. 否则纯 MLE(向后兼容).
        self.shrink_applied = False
        self.shrink_beta = None
        if elo is not None and self.shrinkage_kappa and self.shrinkage_kappa > 0:
            att_log, def_log, beta, _ = _elo_prior_shrink(
                self.teams, P["ih"], P["ia"], P["w"], att_log, def_log,
                elo, self.shrinkage_kappa)
            self.shrink_beta = beta
            self.shrink_applied = True

        self.attack = {t: float(np.exp(al)) for t, al in zip(self.teams, att_log)}
        self.defense = {t: float(np.exp(dl)) for t, dl in zip(self.teams, def_log)}
        self._fitted = True
        self._res = res
        return self

    def fit_at(self, df: pd.DataFrame, as_of: DateLike,
               elo: dict | None = None) -> "DixonColes":
        """as_of 时点快照: 只用 date < as_of 的比赛拟合(防泄露, 供 P0-7 walk-forward).
        elo 同样应传 as_of 时点的 Elo 快照(用 EloModel.ratings_at)."""
        return self.fit(df, as_of=as_of, elo=elo)

    @classmethod
    def from_artifacts(cls, parquet_path, json_path) -> "DixonColes":
        """从持久化产出加载模型 → 可直接 predict, 无需 refit.  (P0-6, P1 API 基础设施)

        输入: dixon_coles_current.parquet(team/attack/defense/n_eff) +
              dixon_coles_global.json(mu/gamma/gamma_host/rho/half_life/...)
        返回: _fitted=True 的 DixonColes, attack/defense/mu/gamma/rho/n_eff 就位,
              half_life/importance/shrink 元信息也从 json 还原(自描述, 供再拟合或展示).
        """
        frame = pd.read_parquet(parquet_path)
        with open(json_path) as f:
            gp = json.load(f)
        m = cls(
            half_life_days=gp.get("half_life_days", DEFAULT_HALF_LIFE_DAYS),
            host_bonus=gp.get("gamma_host", 0.0),
            use_importance_weight=gp.get("importance_weight", True),
            shrinkage_kappa=gp.get("shrinkage_kappa", DEFAULT_SHRINKAGE_KAPPA),
        )
        m.mu = float(gp["mu"])
        m.gamma = float(gp["gamma"])
        m.gamma_host = float(gp["gamma_host"])
        m.rho = float(gp["rho"])
        m.attack = {str(t): float(v) for t, v in zip(frame["team"], frame["attack"])}
        m.defense = {str(t): float(v) for t, v in zip(frame["team"], frame["defense"])}
        m.teams = [str(t) for t in frame["team"]]
        m.neff = ({str(t): float(v) for t, v in zip(frame["team"], frame["n_eff"])}
                  if "n_eff" in frame.columns else {})
        m.shrink_beta = gp.get("shrink_beta")
        m.shrink_applied = gp.get("shrink_applied", False)
        m._fitted = True
        return m

    def shrunk_variant(self, elo: dict, kappa: float) -> "DixonColes":
        """从本 MLE 模型 post-hoc 派生一个收缩变体(不重拟合).  (P0-7 多 κ 对照用)

        复用本模型的 MLE att/def + _P(ih/ia/w), 调 _elo_prior_shrink 施 κ → 新模型.
        一场只需拟合一次 MLE, 即可派生 κ=0(=本模型)/κ=5(收缩)/κ→∞(纯Elo) 三变体.
        返回: 新 DixonColes(全局 μ/γ/ρ 不变, attack/defense 按 κ 收缩), _fitted=True.
        """
        if not self._fitted or self._P is None:
            raise RuntimeError("先 fit()(且 _P 在内存) 再 shrunk_variant().")
        att_log = np.log(np.array([self.attack[t] for t in self.teams]))
        att_log = att_log - att_log.mean()                    # 归一(应已≈0, 兜底)
        def_log = np.log(np.array([self.defense[t] for t in self.teams]))
        att_shr, def_shr, beta, _ = _elo_prior_shrink(
            self.teams, self._P["ih"], self._P["ia"], self._P["w"],
            att_log, def_log, elo, float(kappa))
        m = DixonColes(half_life_days=self.half_life_days, host_bonus=self.gamma_host,
                       use_importance_weight=self.use_importance_weight, shrinkage_kappa=kappa)
        m.mu, m.gamma, m.gamma_host, m.rho = self.mu, self.gamma, self.gamma_host, self.rho
        m.attack = {t: float(np.exp(a)) for t, a in zip(self.teams, att_shr)}
        m.defense = {t: float(np.exp(d)) for t, d in zip(self.teams, def_shr)}
        m.teams, m.neff = list(self.teams), dict(self.neff)
        m.shrink_beta, m.shrink_applied, m._fitted = beta, True, True
        return m

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
        """各队 attack/defense/n_eff → DataFrame(按 net 降序), 便于存 parquet / 展示."""
        if not self._fitted:
            raise RuntimeError("先 fit() 再 to_frame().")
        net = {t: self.attack[t] / self.defense[t] for t in self.teams}
        frame = pd.DataFrame(
            [{"team": t, "attack": self.attack[t], "defense": self.defense[t],
              "net_strength": net[t], "n_eff": self.neff.get(t, 0.0)} for t in self.teams],
            columns=["team", "attack", "defense", "net_strength", "n_eff"],
        )
        return frame.sort_values("net_strength", ascending=False).reset_index(drop=True)

    def global_params(self) -> dict:
        """全局参数 → dict, 供序列化与回测记录(含 P0-5 含金量/收缩元信息)."""
        return {
            "mu": self.mu,
            "gamma": self.gamma,
            "gamma_host": self.gamma_host,
            "rho": self.rho,
            "half_life_days": self.half_life_days,
            "host_fixed": self.host_bonus is not None,
            # P0-5
            "importance_weight": self.use_importance_weight,
            "shrinkage_kappa": self.shrinkage_kappa,
            "shrink_applied": self.shrink_applied,
            "shrink_beta": self.shrink_beta,   # Elo→log强度回归斜率(None=未收缩)
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

    # 生产模型 = 含金量加权 + 纯 MLE(不收缩). 收缩(Elo先验)经 12 场前向验证未见显著收益,
    # 且 48 支 WC 队数据都充足(n_eff 15-25)、DC 无对阵项 → 收缩对 WC 产品近于 no-op,
    # 故默认不注入生产; 作为 safety-net 已实现+测试, 待 P0-7 的 128 场对照定夺(见设计说明第 7 条).
    model = DixonColes().fit(df)   # 含金量加权(默认开) + 纯 MLE

    frame = model.to_frame()
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT_PARQUET, index=False)
    gp = model.global_params()
    with open(OUT_JSON, "w") as f:
        json.dump(gp, f, indent=2)

    print("=" * 64)
    print(f"P0-5 Dixon-Coles 生产模型 | {len(df)} 场 → {len(frame)} 队 "
          f"(含金量加权 + 纯 MLE, 收缩默认关)")
    print(f"全局参数: μ={gp['mu']:.3f}  γ={gp['gamma']:.3f}  "
          f"γ_host={gp['gamma_host']:.3f}  ρ={gp['rho']:.3f}  "
          f"(half_life={gp['half_life_days']:.0f}d)")
    print(f"  解读: 普通主场进球×{np.exp(gp['gamma']):.2f}  "
          f"东道主进球×{np.exp(gp['gamma_host']):.2f}  "
          f"低分修正 ρ{'<' if gp['rho']<0 else '>'}0 "
          f"({'符合预期' if gp['rho'] < 0 else '⚠️ 与典型相反, 回测留意'})")
    print(f"  含金量: 友谊赛/邀请杯=0.25, Olympic U-23/*Games/CONIFA 已剔除(w=0) | "
          f"收缩 safety-net: 关(传 elo 可开, κ={gp['shrinkage_kappa']:.0f})")
    print("=" * 64)
    # 主流国家队(对齐"聚焦参赛 48 队"); n_eff = 数据充足度(越大参数越稳)
    showcase = ["Spain", "Argentina", "France", "England", "Brazil", "Germany",
                "Netherlands", "Portugal", "Belgium", "Italy",
                "United States", "Canada", "Mexico"]
    sc = frame[frame["team"].isin(showcase)].copy()
    order = {t: i for i, t in enumerate(showcase)}
    sc = sc.assign(_o=sc["team"].map(order)).sort_values("_o")[
        ["team", "attack", "defense", "net_strength", "n_eff"]]
    print("主流国家队 attack/defense (net=attack/defense, n_eff=有效样本量):")
    print(sc.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("-" * 64)
    # —— P0-5 收缩 safety-net 诊断(证明可用, 非生产默认): 拟合含 Elo 先验版, 看 ConIFA 小队被去污染 ——
    from elo import EloModel  # noqa: E402
    elo = EloModel().fit(df).ratings()
    model_shr = DixonColes().fit(df, elo=elo)   # 同数据 + Elo 先验收缩(κ=5)
    shr_frame = model_shr.to_frame()
    print(f"收缩 safety-net 诊断(开 κ={model_shr.shrinkage_kappa:.0f}, β={model_shr.shrink_beta:.4f}): "
          f"n_eff 最低 8 队收缩前后 net_strength 对比")
    diag = shr_frame.nsmallest(8, "n_eff")[["team", "n_eff"]].copy()
    diag["net_收缩前(MLE)"] = diag["team"].map({t: frame.loc[frame.team == t, "net_strength"].iloc[0]
                                                  for t in diag["team"]})
    diag["net_收缩后"] = shr_frame.nsmallest(8, "n_eff")["net_strength"].values
    print(diag.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("  → 小样本/业余队(ConIFA)被拉向 Elo 先验 net≈1.0, 不再离谱极值污染(此为 safety-net, 生产默认关)")
    print("-" * 64)

    # 2026 三东道主: 纯中立 vs 本国主场(享普通 γ; γ_host 已砍见设计说明第 2 条), 对手 Switzerland
    hosts = ["United States", "Canada", "Mexico"]
    opp = "Switzerland"
    print(f"2026 东道主: 纯中立 vs 本国主场(γ={model.gamma:.3f}) 对照, 对手 {opp}:")
    for h in hosts:
        if h not in model.attack:
            print(f"  {h}: 不在历史数据(跳过)")
            continue
        p_neu = model.predict(h, opp, neutral=True)     # 纯中立(基准)
        p_home = model.predict(h, opp, neutral=False)    # 本国主场: 享 γ
        print(f"  {h:14s} 纯中立 胜率={p_neu['home_win']:5.1%} (λ={p_neu['lambda_home']:.2f})"
              f"  →  本国主场 胜率={p_home['home_win']:5.1%} (λ={p_home['lambda_home']:.2f})"
              f"  Δ=+{(p_home['home_win']-p_neu['home_win'])*100:4.1f}pp (仅 γ)")
    print("-" * 64)
    print(f"已存: {OUT_PARQUET.name} + {OUT_JSON.name}")
    print("=" * 64)

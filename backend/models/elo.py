"""
P0-3 Elo Rating 实现
==========================================
方法论: World Football Elo Rating (Wikipedia / eloratings.net 标准)
输入: data/processed/international_history.parquet  (P0-2 产出, 49417 场已完赛)
产出: data/processed/elo_current.parquet            (全量训练后的当前 Elo 排名)

更新公式:  ΔR = K · G · (W − We)
  K   = 赛事等级权重        k_factor()
  G   = 净胜球倍数          goal_multiplier()
  W   = 实际结果(主队视角)   result_value()   胜=1 / 平=0.5 / 负=0
  We  = 主队期望胜率        expected()        1 / (1 + 10^(-(R_h − R_a + H)/400))
  H   = 主场优势(分)        home_advantage_for()  含高原修正

设计说明
--------
1. 【可选半衰期时间衰减, 默认关】Elo 的"近期性"默认靠滚动更新本身实现. 可选
   half_life 参数给 delta 加指数衰减 exp(-ln2·Δt/half_life)(Δt = ref_date − match_date),
   让旧比赛贡献衰减、近期比赛权重更高(修"强队近期下滑"反应迟钝, 见 memory
   elo-recency-lag-brazil). 默认 half_life=0 = 不衰减(生产 κ=20 链路不变). 衰减基准
   ref_date 应取评估时点 as_of(对齐 DC 的 time_decay_weights), 防未来信息泄露.
2. 【防数据泄露】ratings_at(df, as_of) 只用严格早于 as_of 的比赛重算 → 时点快照,
   直接支撑 P0-7 的 walk-forward 回测(测试集比赛不污染训练).
   注: 每次 ratings_at 都从头重算一次(O(N)); 高频回测(数百场)建议在 P0-7 改用
   顺序遍历一次的模式(遇回测场先预测、再更新), 性能更好.
3. 【主场优势 H】= 基础 65 分(海平面主场) + 高原加成(见下). neutral=True 时 H=0.
4. 【高原主场修正 ⭐】玻利维亚(拉巴斯 3600m)/厄瓜多尔(基多 2850m)等高原主场对
   客队是系统性巨大劣势: 实测玻利维亚主场胜率加成 +41.5%(联盟均值仅 +12.9%).
   若 H 固定 65, 高原赢球会被当成"超预期"灌入过多 Elo → 世界杯是纯中立场,
   这些队拿不到高原加成, 预测时会被系统性高估胜率.
   修正: home_advantage_for(city) 让 H 随城市海拔升高 → 高原主场的真实优势
   进入 We → ΔR unbiased → 世界杯用 H=0 预测时, 高原队的 Elo 是"纯实力".
   实测排除: 哥伦比亚主场实际在海平面 Barranquilla(非 Bogotá), 秘鲁在 Lima,
   故它们不受影响, 水分仅集中在玻利维亚/厄瓜多尔/墨西哥等少数场地.
   系数 ALTITUDE_PER_KM 为实测初估, 可在 P0-8 回测时精调.
5. 【初始 rating】1500(World Football Elo 标准); 新队首场从 1500 起步.

运行自检: .venv/bin/python backend/models/elo.py
跑测试:   .venv/bin/python -m unittest backend.models.test_elo
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Union

import numpy as np
import pandas as pd

LN2 = float(np.log(2.0))

# ============================================================
# 常量
# ============================================================
INITIAL_RATING = 1500.0
HOME_ADVANTAGE = 65.0  # 普通主场(海平面)基础优势(分); 可调
DEFAULT_ELO_HALF_LIFE_DAYS = 0.0   # Elo 时间衰减半衰期(天); 0=不衰减(生产默认). >0 时旧比赛 delta 按 exp(-ln2·Δt/half_life) 衰减, 修"强队近期下滑"反应迟钝(见 memory elo-recency-lag-brazil)

# —— 高原主场修正 (见设计说明第 4 条) ——
# 海拔超过阈值的场地, H 按 ALTITUDE_PER_KM(分/千米)额外递增,
# 把高原主场的真实优势计入 We, 避免给主队灌入过多 Elo(世界杯中立场会高估).
ALTITUDE_THRESHOLD = 1500.0   # 米, 高原判定线
ALTITUDE_PER_KM = 50.0        # 每超 1km 海拔额外加的主场优势分(实测初估, 回测可调)
# 已知高原主场城市 → 海拔(米). 来源: martj42 city 列实测分布 + 地理常识.
# 注: 哥伦比亚主场实际在海平面 Barranquilla, 秘鲁在 Lima, 故它们不在此表.
ALTITUDE_VENUES = {
    "El Alto": 4100,           # 玻利维亚(最高)
    "La Paz": 3600,            # 玻利维亚
    "Cusco": 3400,             # 秘鲁(偶用)
    "Quito": 2850,             # 厄瓜多尔
    "Toluca": 2680,            # 墨西哥
    "Bogotá": 2640,            # 哥伦比亚(偶用)
    "Cuenca": 2500,            # 厄瓜多尔
    "Mexico City": 2240,       # 墨西哥
    "San Luis Potosí": 1860,   # 墨西哥
}

# K 因子分级 —— C 方案: 显式桶(高频正赛逐一列出) + 关键词兜底(预选赛/友谊赛) + 默认档
# 这样 200 种赛事里那 119 种小赛事(占总场次 2.4%)全部干净落到 30 档, 0% 漏判.
TIER_60 = {"FIFA World Cup"}  # 世界杯正赛(最重要)
TIER_50 = {                   # 洲际大赛正赛(决赛圈)
    "UEFA Euro",
    "Copa América",
    "African Cup of Nations",
    "AFC Asian Cup",
    "Gold Cup",
    "Oceania Nations Cup",
    "CONCACAF Championship",   # 历史叫法(=Gold Cup 前身)
    "Confederations Cup",
}

DateLike = Union[date, datetime, str, pd.Timestamp]


# ============================================================
# 纯函数层
# ============================================================
def k_factor(tournament: str) -> int:
    """赛事等级 → K 因子(更新步长).

    60  世界杯正赛
    50  洲际大赛正赛(Euro/Copa/AFCON/Asian Cup/Gold Cup/...)
    40  所有预选赛(信号弱于正赛但强于友谊赛)
    20  友谊赛
    30  其余正式锦标赛(Nations League / 区域性杯赛等) —— 默认兜底档
    """
    if tournament in TIER_60:
        return 60
    if tournament in TIER_50:
        return 50
    if "qualification" in tournament.lower():
        return 40
    if tournament == "Friendly":
        return 20
    return 30


def goal_multiplier(goal_diff: int) -> float:
    """净胜球倍数 G (World Football Elo 标准).

    |Δ| = 0 或 1 → 1.0   (普通结果)
    |Δ| = 2       → 1.5   (明确胜利, 加重)
    |Δ| ≥ 3       → (11 + |Δ|) / 8, 封顶 ≈2.0 (大胜/惨败, 大幅加权)
    对称: 胜 3 球与负 3 球的 G 相同(符号由 W−We 体现).
    """
    d = abs(goal_diff)
    if d <= 1:
        return 1.0
    if d == 2:
        return 1.5
    return (11.0 + d) / 8.0  # 3→1.75, 4→1.875, 5→2.0, 6→2.125…


def expected(r_home: float, r_away: float, home_adv: float = HOME_ADVANTAGE) -> float:
    """主队期望胜率 We = 1 / (1 + 10^(-(R_h − R_a + H)/400)).

    H 加在主队侧(主队享主场优势); 中立场调用方传 home_adv=0.
    两队等分且中立 → 0.5; 差 400 分 → ~0.909.
    """
    return 1.0 / (1.0 + 10.0 ** (-(r_home - r_away + home_adv) / 400.0))


def home_advantage_for(city: object, neutral: bool) -> float:
    """该场比赛的主场优势 H(分).

    neutral=True → 0 (中立场, 世界杯场景)
    否则 → HOME_ADVANTAGE(65) + 高原加成(城市海拔 > 阈值时按 ALTITUDE_PER_KM 递增)
    未知城市按海平面处理(无加成).
    """
    if neutral:
        return 0.0
    alt = ALTITUDE_VENUES.get(city, 0) if city else 0
    if alt < ALTITUDE_THRESHOLD:
        return HOME_ADVANTAGE
    return HOME_ADVANTAGE + ALTITUDE_PER_KM * (alt - ALTITUDE_THRESHOLD) / 1000.0


def result_value(home_score: int, away_score: int) -> float:
    """实际结果 W(主队视角): 胜=1.0 / 平=0.5 / 负=0.0."""
    if home_score > away_score:
        return 1.0
    if home_score < away_score:
        return 0.0
    return 0.5


# ============================================================
# EloModel —— 有状态地维护各队 rating, 支持时点快照
# ============================================================
class EloModel:
    """维护各队 Elo rating, 支持滚动更新与时点快照查询.

    典型用法:
        m = EloModel()
        m.fit(df)                          # 按日期升序全量滚动更新
        m.ratings()                        # 当前全部 rating(dict)
        m.ratings_at(df, "2022-01-01")     # 该时点快照(只用更早的比赛, 防泄露)
    """

    def __init__(
        self,
        initial: float = INITIAL_RATING,
        home_adv: float = HOME_ADVANTAGE,  # 保留供单测/调参; 运行时 H 由 home_advantage_for 按城市算
    ) -> None:
        self.initial = initial
        self.home_adv = home_adv
        self._ratings: dict[str, float] = {}  # 当前最新 rating

    # ---------- 内部 ----------
    def _get(self, team: str) -> float:
        return self._ratings.get(team, self.initial)

    def _apply_one(
        self,
        home: str,
        away: str,
        home_goals: int,
        away_goals: int,
        tournament: str,
        neutral: bool,
        city: object = None,
        match_date: DateLike | None = None,
        ref_date: DateLike | None = None,
        half_life: float = 0.0,
    ) -> None:
        """单场比赛 → 双向更新 rating(零和: 主队+Δ 等于客队−Δ).
        H 由城市海拔决定(home_advantage_for), 高原主场享更大主场优势 → ΔR unbiased.
        half_life>0 且 match_date/ref_date 齐时, delta 按时间衰减 exp(-ln2·Δt/half_life)
        缩放(双方同权重, 零和守恒). half_life=0 或日期缺 → 不衰减."""
        rh, ra = self._get(home), self._get(away)
        H = home_advantage_for(city, neutral)
        we = expected(rh, ra, H)
        w = result_value(home_goals, away_goals)
        delta = k_factor(tournament) * goal_multiplier(home_goals - away_goals) * (w - we)
        if half_life and half_life > 0 and match_date is not None and ref_date is not None:
            dt_days = (pd.Timestamp(ref_date) - pd.Timestamp(match_date)).total_seconds() / 86400.0
            delta *= float(np.exp(-LN2 * max(0.0, dt_days) / half_life))
        self._ratings[home] = rh + delta
        self._ratings[away] = ra - delta

    # ---------- 公开 API ----------
    def fit(self, df: pd.DataFrame, ref_date: DateLike | None = None,
            half_life: float = 0.0) -> "EloModel":
        """按日期升序滚动更新全部比赛. 需含列:
        date / home_team / away_team / home_score / away_score / tournament / neutral / city.
        ref_date/half_life>0 时每场 delta 按时间衰减(基准 ref_date, 近期加权)."""
        cols = ["date", "home_team", "away_team", "home_score",
                "away_score", "tournament", "neutral", "city"]
        rows = df[cols].sort_values("date").itertuples(index=False)
        for r in rows:
            self._apply_one(
                r.home_team, r.away_team,
                int(r.home_score), int(r.away_score),
                r.tournament, bool(r.neutral), r.city,
                match_date=r.date, ref_date=ref_date, half_life=half_life,
            )
        return self

    def get(self, team: str) -> float:
        """单队当前 rating(未出现过的新队返回初始分)."""
        return self._get(team)

    def ratings(self) -> dict[str, float]:
        """当前全部 rating 的拷贝."""
        return dict(self._ratings)

    def ratings_at(self, df: pd.DataFrame, as_of: DateLike,
                   half_life: float = 0.0) -> dict[str, float]:
        """as_of 时点的 rating 快照: 从空状态, 仅用 date < as_of 的比赛重算.

        → 防数据泄露: 严格排除 as_of 当天及之后的比赛(含测试集本身).
        half_life>0 时启用时间衰减(基准=as_of, 对齐 DC time_decay), 让近期比赛权重更高.
        每次调用重算一次 O(N); 数百场的 walk-forward 回测建议在 P0-7 改顺序遍历.
        """
        cutoff = pd.Timestamp(as_of)
        hist = df[df["date"] < cutoff]
        snap = EloModel(self.initial, self.home_adv)
        snap.fit(hist, ref_date=cutoff, half_life=half_life)
        return snap.ratings()

    def to_frame(self) -> pd.DataFrame:
        """当前排名 → DataFrame(team, elo), 按 elo 降序. 便于存 parquet / 展示."""
        frame = pd.DataFrame(
            [{"team": t, "elo": r} for t, r in self._ratings.items()],
            columns=["team", "elo"],
        )
        return frame.sort_values("elo", ascending=False).reset_index(drop=True)


# ============================================================
# 自检: 训练全量历史 → 打印 Top 20 + 存 elo_current.parquet
# ============================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    HIST = ROOT / "data" / "processed" / "international_history.parquet"
    OUT = ROOT / "data" / "processed" / "elo_current.parquet"

    if not HIST.exists():
        sys.exit(f"[ERR] 找不到 {HIST}，请先跑 P0-2 (backend/data/load_results.py)")

    df = pd.read_parquet(HIST)
    model = EloModel().fit(df)
    frame = model.to_frame()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT, index=False)

    print("=" * 60)
    print(f"P0-3 Elo 训练完成 | {len(df)} 场 → {len(frame)} 队 (含高原主场修正)")
    print(f"rating 范围: {frame['elo'].min():.1f} ~ {frame['elo'].max():.1f}"
          f" | 均值 {frame['elo'].mean():.1f}")
    print("=" * 60)
    print("当前 Elo Top 20:")
    print(frame.head(20).to_string(index=False))
    print("-" * 60)
    # 高原队修正前后对照(本表为修正后; 若想看修正量级, 跑无修正版对比)
    alt_teams = ["Bolivia", "Ecuador", "Mexico", "Colombia"]
    print("高原相关队(修正后):")
    for t in alt_teams:
        row = frame[frame["team"] == t]
        if len(row):
            print(f"  {t:10s} #{int(row.index[0])+1:>2}  Elo={row['elo'].values[0]:.1f}")
    print("-" * 60)
    print(f"已存: {OUT.name}")
    print("=" * 60)

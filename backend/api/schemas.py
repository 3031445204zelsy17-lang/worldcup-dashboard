"""
P1-4 API Pydantic 响应模型
=========================
FastAPI ``response_model`` —— 自动 OpenAPI 文档 + P1-5 前端类型生成.
Pydantic v2 语法(field: type). 字段 snake_case(前端直接消费).

与 docs/wireframes.md 的 4 页面对齐: 总览(Health/Teams/Tournament)、
球队详情(TeamDetail)、比赛详情(MatchDetail/Prediction)、方法论(Methodology).
"""
from __future__ import annotations

from pydantic import BaseModel


# ---------------- 通用 ----------------
class HealthResponse(BaseModel):
    status: str
    db_readable: bool
    counts: dict
    last_mc_recomputed_at: str | None = None
    dc_artifacts_loaded: bool


class TeamBrief(BaseModel):
    name: str
    group: str | None = None
    elo: float | None = None


class TeamRanked(TeamBrief):
    rank: int


class TeamsResponse(BaseModel):
    teams: list[TeamRanked]
    count: int


# ---------------- 锦标赛概率 ----------------
class Advancement(BaseModel):
    """单队 6 轮晋级概率(group=出线恒 1).不含 win(win 单列)."""
    group: float
    ro32: float
    ro16: float
    qf: float
    sf: float
    final: float


class TournamentTeam(BaseModel):
    name: str
    group: str | None = None
    elo: float | None = None
    win_prob: float
    advancement: Advancement
    sort_value: float          # 当前 view 下的排序值(前端高亮用)
    ci_low: float | None = None    # sort_value 的 95% Wilson 区间下界(MC 抽样不确定性)
    ci_high: float | None = None
    diff: float | None = None      # 最近一场赛果导致的变化(pp, 正=涨/负=跌); None=无历史可算


class TournamentResponse(BaseModel):
    view: str
    last_recomputed_at: str | None = None
    teams: list[TournamentTeam]


class StepItem(BaseModel):
    """晋级阶梯一格(含 95% Wilson 置信区间)."""
    round: str
    prob: float
    ci_low: float
    ci_high: float
    label: str


# ---------------- 比赛 ----------------
class MatchSummary(BaseModel):
    match_key: str
    date: str
    kickoff: str | None = None
    home: str
    away: str
    home_score: int | None = None
    away_score: int | None = None
    status: str
    neutral: bool
    stage: str | None = None


class MatchesResponse(BaseModel):
    filters: dict
    count: int
    matches: list[MatchSummary]


class Prediction(BaseModel):
    home: str
    away: str
    neutral: bool
    host_home: bool
    host_away: bool
    home_win: float
    draw: float
    away_win: float
    lambda_home: float
    lambda_away: float
    expected_home: float
    expected_away: float
    top_scores: list[list]            # [[home_goals, away_goals, prob], ...]
    score_matrix: list[list[float]]   # (max_goals+1)×(max_goals+1)
    max_goals: int


class Score(BaseModel):
    home: int
    away: int


class Drivers(BaseModel):
    """驱动因素(P1-6: elo + DC 攻防参数就绪; altitude/weather/injuries 留 P2 → null)."""
    home_elo: float | None = None
    away_elo: float | None = None
    elo_gap: float | None = None
    neutral: bool | None = None
    host_advantage: bool | None = None
    altitude: float | None = None
    weather: dict | None = None
    injuries: dict | None = None
    recent_form: float | None = None
    elo_gap_vs_avg: float | None = None
    # DC 攻防参数(λ 可解释; 比赛用 home_/away_, 球队用单值)
    home_attack: float | None = None
    home_defense: float | None = None
    away_attack: float | None = None
    away_defense: float | None = None
    home_attack_rank: int | None = None
    home_defense_rank: int | None = None
    away_attack_rank: int | None = None
    away_defense_rank: int | None = None
    attack: float | None = None
    defense: float | None = None
    attack_rank: int | None = None
    defense_rank: int | None = None
    global_mu: float | None = None
    global_gamma: float | None = None
    data_status: str = "pending"


class MatchDetailResponse(BaseModel):
    match: MatchSummary
    prediction: Prediction | None = None
    score: Score | None = None
    drivers: Drivers


class TeamDetailResponse(BaseModel):
    name: str
    group: str | None = None
    elo: float | None = None
    rank: int
    advancement_path: list[StepItem]
    matches: list[MatchSummary]
    drivers: Drivers


class HistorySnapshot(BaseModel):
    """一次 MC 重算快照(该队在那一刻的夺冠 + 各轮晋级概率)."""
    calculated_at: str
    win_prob: float
    advancement: dict


class HistoryResponse(BaseModel):
    team: str
    snapshots: list[HistorySnapshot]


# ---------------- 方法论 ----------------
class MethodologyResponse(BaseModel):
    algorithm_chain: list[str]
    accuracy: dict | None = None
    calibration: list[dict]
    data_window: str
    disclaimer: str

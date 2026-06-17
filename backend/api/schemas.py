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


class TournamentResponse(BaseModel):
    view: str
    last_recomputed_at: str | None = None
    teams: list[TournamentTeam]


class StepItem(BaseModel):
    """晋级阶梯一格."""
    round: str
    prob: float
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
    """驱动因素(P1 阶段多数字段空 → null + data_status)."""
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


# ---------------- 方法论 ----------------
class MethodologyResponse(BaseModel):
    algorithm_chain: list[str]
    accuracy: dict | None = None
    calibration: list[dict]
    data_window: str
    disclaimer: str

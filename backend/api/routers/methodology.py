"""GET /api/methodology —— 方法论文案 + 回测准确率 + 校准(reliability)数据.

accuracy 为 None 当 backtest parquet 未生成(部署环境未跑 P0-7/P0-8)—— 前端降级显示.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.api import queries
from backend.api.schemas import MethodologyResponse

router = APIRouter(tags=["methodology"])


@router.get("/api/methodology", response_model=MethodologyResponse)
def methodology() -> dict:
    return {
        "algorithm_chain": ["Elo", "Dixon-Coles (+shrinkage κ=20)", "Monte-Carlo 10000x"],
        "accuracy": queries.backtest_summary(),          # dict | None
        "calibration": queries.calibration_rows(),       # list[dict](可能空)
        "data_window": "4 年国际赛(49417 场 / 336 队)",
        "disclaimer": "分析工具, 非博彩建议. 概率有不确定性, 足球预测天花板 ~53-55%.",
    }

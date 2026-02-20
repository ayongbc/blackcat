"""
选股策略模块
每个策略实现 screen() 接口，返回当日股票池
"""

from .base import BaseStrategy
from .pullback_ma120 import PullbackMA120Strategy
from .trend_ma import TrendMAStrategy

STRATEGIES = {
    "pullback_ma120": PullbackMA120Strategy,
    "trend_ma": TrendMAStrategy,
}


def get_strategy(name: str) -> type[BaseStrategy]:
    if name not in STRATEGIES:
        raise ValueError(f"未知策略: {name}，可选: {list(STRATEGIES.keys())}")
    return STRATEGIES[name]

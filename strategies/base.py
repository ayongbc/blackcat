"""
选股策略基类
"""

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseStrategy(ABC):
    """选股策略抽象基类"""

    @abstractmethod
    def screen(
        self,
        date: str,
        universe_df: pd.DataFrame,
        get_kline_fn: callable,
        benchmark_ret20: float,
    ) -> list[dict[str, Any]]:
        """
        对给定日期执行选股
        :param date: 筛选日期 (YYYY-MM-DD)
        :param universe_df: 股票列表 DataFrame (code, code_name, ipoDate)
        :param get_kline_fn: 函数 (code, end_date) -> DataFrame
        :param benchmark_ret20: 基准 20 日收益率
        :return:  list[dict]，每个 dict 至少含 code, name, score
        """
        pass

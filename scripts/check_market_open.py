#!/usr/bin/env python3
"""
开盘日判断脚本
检查当前日期是否为A股开盘日
"""

from datetime import datetime, timedelta
import sys

# 中国A股2026年节假日（需维护）
HOLIDAYS = [
    "2026-01-01",  # 元旦
    "2026-01-26",  # 春节假期
    "2026-01-27",
    "2026-01-28",
    "2026-01-29",
    "2026-01-30",
    "2026-01-31",
    "2026-02-01",
    "2026-02-02",
    "2026-02-03",
    "2026-02-04",
    "2026-02-05",
    "2026-02-06",
    "2026-02-07",
    "2026-02-08",
    "2026-02-09",
    "2026-02-10",
    "2026-02-11",
    "2026-02-12",
    "2026-02-13",
    "2026-02-14",
    "2026-02-15",
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",  # 春节结束（2月20日开市）
    # 清明节
    "2026-04-04",
    "2026-04-05",
    "2026-04-06",
    # 劳动节
    "2026-05-01",
    "2026-05-02",
    "2026-05-03",
    "2026-05-04",
    "2026-05-05",
    # 端午节
    "2026-05-30",
    "2026-05-31",
    "2026-06-01",
    # 中秋节
    "2026-09-25",
    "2026-09-26",
    "2026-09-27",
    # 国庆节
    "2026-10-01",
    "2026-10-02",
    "2026-10-03",
    "2026-10-04",
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
]

def is_market_open(date=None):
    """
    判断是否为A股开盘日
    
    Args:
        date: datetime对象，默认当前时间
        
    Returns:
        bool: True为开盘日，False为不开盘
    """
    if date is None:
        date = datetime.now()
    
    # 1. 周末检查（周六=5，周日=6）
    if date.weekday() >= 5:
        print(f"{date.strftime('%Y-%m-%d')} 是周末，不开盘")
        return False
    
    # 2. 节假日检查
    date_str = date.strftime("%Y-%m-%d")
    if date_str in HOLIDAYS:
        print(f"{date_str} 是节假日，不开盘")
        return False
    
    # 3. 特殊休市日检查（可扩展）
    # 例如：调休补班日
    
    print(f"{date_str} 是开盘日")
    return True


def is_market_open_date_str(date_str):
    """
    用日期字符串判断是否为开盘日
    
    Args:
        date_str: 日期字符串，格式如 "2026-02-19"
        
    Returns:
        bool: True为开盘日，False为不开盘
    """
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        return is_market_open(date)
    except ValueError:
        print(f"日期格式错误: {date_str}")
        return False


if __name__ == "__main__":
    # 支持命令行参数
    if len(sys.argv) > 1:
        # 指定日期判断
        date_str = sys.argv[1]
        result = is_market_open_date_str(date_str)
    else:
        # 判断今天是否开盘
        result = is_market_open()
    
    # 返回状态码
    sys.exit(0 if result else 1)

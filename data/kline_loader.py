"""
K线数据加载与获取模块
支持按日期范围加载，供回测和日频筛选复用
"""

import os
import time
from typing import Optional

import pandas as pd
import baostock as bs

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
KLINE_DIR = os.path.join(DATA_DIR, "kline")


def _ensure_kline_dir():
    os.makedirs(KLINE_DIR, exist_ok=True)


def fetch_kline(
    code: str,
    start_date: str,
    end_date: str,
    adjustflag: str = "2",
) -> pd.DataFrame:
    """从 BaoStock 拉取 K 线数据"""
    fields = "date,code,open,high,low,close,volume,amount"
    rs = bs.query_history_k_data_plus(
        code,
        fields,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag=adjustflag,
    )

    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=fields.split(","))
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_kline(
    code: str,
    end_date: str,
    start_date: Optional[str] = None,
    adjustflag: str = "2",
    lookback_days: int = 420,
) -> pd.DataFrame:
    """
    加载 K 线：优先读本地缓存，缺失时从 API 拉取并合并
    - end_date: 截止日期
    - start_date: 起始日期（可选，不指定则用 lookback_days 推算）
    """
    _ensure_kline_dir()
    path = os.path.join(KLINE_DIR, code.replace(".", "_") + ".csv")

    if start_date is None:
        start_date = (
            pd.to_datetime(end_date) - pd.Timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

    if os.path.exists(path):
        old = pd.read_csv(path, parse_dates=["date"])
        old["date"] = pd.to_datetime(old["date"])
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        if old["date"].min() <= start_dt and old["date"].max() >= end_dt:
            return old[old["date"] <= end_dt].copy()

        # 需要补充：向后延伸或向前延伸
        first, last = old["date"].min(), old["date"].max()
        if last < end_dt:
            start_fetch = (last + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            new = fetch_kline(code, start_fetch, end_date, adjustflag)
            df = (
                pd.concat([old, new], ignore_index=True)
                .drop_duplicates(subset=["date"])
                .sort_values("date")
            )
        elif first > start_dt:
            end_fetch = (first - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            new = fetch_kline(code, start_date, end_fetch, adjustflag)
            df = (
                pd.concat([new, old], ignore_index=True)
                .drop_duplicates(subset=["date"])
                .sort_values("date")
            )
        else:
            return old[old["date"] <= end_dt].copy()
    else:
        df = fetch_kline(code, start_date, end_date, adjustflag)

    if len(df) > 0:
        df.to_csv(path, index=False)

    return df[df["date"] <= end_date].copy() if len(df) > 0 else df


def load_kline_for_backtest(
    code: str,
    as_of_date: str,
    min_bars: int = 160,
    adjustflag: str = "2",
) -> pd.DataFrame:
    """回测用：加载截至 as_of_date 的 K 线，保证至少 min_bars 条"""
    lookback = max(500, min_bars + 100)  # 多取一些以覆盖 120MA
    return load_kline(code, as_of_date, lookback_days=lookback, adjustflag=adjustflag)


def supplement_kline(
    codes: list[str],
    start_date: str,
    end_date: str,
    adjustflag: str = "2",
    sleep_s: float = 0.02,
) -> int:
    """
    批量补充 K 线数据到本地
    返回实际发起 API 请求的次数
    """
    _ensure_kline_dir()
    api_count = 0

    for i, code in enumerate(codes):
        if (i + 1) % 50 == 0:
            print(f"  已处理 {i + 1}/{len(codes)} 只...", flush=True)

        path = os.path.join(KLINE_DIR, code.replace(".", "_") + ".csv")
        need_fetch = True

        if os.path.exists(path):
            old = pd.read_csv(path, parse_dates=["date"])
            old["date"] = pd.to_datetime(old["date"])
            first = old["date"].min()
            last = old["date"].max()
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)

            if first <= start_dt and last >= end_dt:
                need_fetch = False
            else:
                if last < end_dt:
                    start_date_actual = (last + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    new = fetch_kline(code, start_date_actual, end_date, adjustflag)
                    api_count += 1
                    if len(new) > 0:
                        df = (
                            pd.concat([old, new], ignore_index=True)
                            .drop_duplicates(subset=["date"])
                            .sort_values("date")
                        )
                        df.to_csv(path, index=False)
                if first > start_dt:
                    end_date_actual = (first - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    new = fetch_kline(code, start_date, end_date_actual, adjustflag)
                    api_count += 1
                    if len(new) > 0:
                        old = pd.read_csv(path, parse_dates=["date"])
                        df = (
                            pd.concat([new, old], ignore_index=True)
                            .drop_duplicates(subset=["date"])
                            .sort_values("date")
                        )
                        df.to_csv(path, index=False)
                need_fetch = False

        if need_fetch:
            df = fetch_kline(code, start_date, end_date, adjustflag)
            api_count += 1
            if len(df) > 0:
                df.to_csv(path, index=False)

        if sleep_s > 0 and api_count > 0:
            time.sleep(sleep_s)

    return api_count

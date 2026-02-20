"""
选股宇宙与股票列表
支持指定日期获取历史成分股（BaoStock 支持，zz1000/zz2000 使用当日成分近似）
"""

from typing import Optional

import pandas as pd
import baostock as bs

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


def _akshare_code_to_baostock(s: str) -> str:
    s = str(s).strip().upper()
    if ".SH" in s:
        return "sh." + s.split(".SH")[0]
    if ".SZ" in s:
        return "sz." + s.split(".SZ")[0]
    if ".BJ" in s:
        return "bj." + s.split(".BJ")[0]
    if s.startswith("6"):
        return "sh." + s
    return "sz." + s


def get_trading_dates(start_date: str, end_date: str) -> list[str]:
    """获取 [start_date, end_date] 区间内的所有交易日"""
    rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
    tds = []
    while rs.error_code == "0" and rs.next():
        tds.append(rs.get_row_data())
    df = pd.DataFrame(tds, columns=rs.fields)
    df = df[df["is_trading_day"] == "1"].sort_values("calendar_date")
    return df["calendar_date"].astype(str).tolist()


def last_trading_date() -> str:
    today = __import__("datetime").date.today().strftime("%Y-%m-%d")
    rs = bs.query_trade_dates(
        start_date=(__import__("datetime").date.today() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d"),
        end_date=today,
    )
    tds = []
    while rs.error_code == "0" and rs.next():
        tds.append(rs.get_row_data())
    df_td = pd.DataFrame(tds, columns=rs.fields)
    df_td = df_td[df_td["is_trading_day"] == "1"]
    if df_td.empty:
        return today
    return str(df_td.iloc[-1]["calendar_date"])


def get_universe_codes(universe: str, query_date: Optional[str] = None) -> set[str]:
    """
    获取选股宇宙成分股代码
    - hs300/zz500/sz50: 使用 BaoStock，支持历史 query_date
    - zz1000/zz2000: 使用 AKShare，成分股为当前（历史回测时为近似）
    """
    if query_date is None:
        query_date = last_trading_date()

    if universe == "all":
        return set()

    if universe in ("zz1000", "zz2000"):
        if not HAS_AKSHARE:
            raise RuntimeError(f"universe={universe} 需安装 akshare")
        symbol = "000852" if universe == "zz1000" else "932000"
        try:
            df = ak.index_stock_cons_weight_csindex(symbol=symbol)
        except Exception as e:
            raise RuntimeError(f"AKShare 拉取 {universe} 成分股失败: {e}") from e
        col = None
        for c in ("成分券代码", "con_code", "code"):
            if c in df.columns:
                col = c
                break
        if col is None:
            raise RuntimeError(f"未找到成分股代码列: {list(df.columns)}")
        return {_akshare_code_to_baostock(x) for x in df[col].dropna().astype(str)}

    if universe == "hs300":
        rs = bs.query_hs300_stocks(date=query_date)
    elif universe == "zz500":
        rs = bs.query_zz500_stocks(date=query_date)
    elif universe == "sz50":
        rs = bs.query_sz50_stocks(date=query_date)
    else:
        raise ValueError(f"Unknown universe: {universe}")

    codes = set()
    while rs.error_code == "0" and rs.next():
        codes.add(rs.get_row_data()[1])
    return codes


def get_stock_list(
    universe: str,
    query_date: Optional[str] = None,
    allow_bj: bool = False,
    as_of_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    获取过滤后的股票列表
    过滤：status=1、非ST、非退市、可选排除北交所
    as_of_date: 若指定，则排除 ipoDate 晚于该日的次新股（回测用）
    """
    allowed = get_universe_codes(universe, query_date)

    rs = bs.query_stock_basic()
    data = []
    while rs.error_code == "0" and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=rs.fields)

    if allowed:
        df = df[df["code"].isin(allowed)].copy()

    df = df[df["status"] == "1"].copy()
    df = df[~df["code_name"].str.contains("ST", na=False)]
    df = df[~df["code_name"].str.contains("退", na=False)]

    if not allow_bj:
        df = df[~df["code"].str.startswith("bj.")]

    if as_of_date is not None and "ipoDate" in df.columns:
        # 排除 as_of_date 之后上市的
        df["ipoDate"] = pd.to_datetime(df["ipoDate"], errors="coerce")
        as_dt = pd.to_datetime(as_of_date)
        df = df[(df["ipoDate"].isna()) | (df["ipoDate"] <= as_dt)].copy()

    return df[["code", "code_name", "ipoDate"]].reset_index(drop=True)

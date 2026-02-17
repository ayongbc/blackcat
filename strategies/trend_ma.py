"""
趋势均线选股策略
与 run_daily.py 中的逻辑一致，通过 config 可配置
支持 pool_workers > 1 时多进程并行评分以加速回测
"""

import math
import os
from multiprocessing import Pool
from typing import Any

import pandas as pd

from .base import BaseStrategy

# 多进程时每个子进程需独立登录 BaoStock，用 pid 记录避免重复登录
_bs_login_pid = None

# 默认策略参数（可被 bt_config 覆盖）
DEFAULT_CONFIG = {
    "min_avg_amount_20": 5e7,
    "min_bars": 160,
    "pullback_touch_tol": 0.02,
    "breakout_vol_ratio": 1.5,
    "max_close_over_ma20": 1.08,
    "min_rs20": 0.0,
    "min_days_above_ma120": 30,
    "min_trend_days": 10,
    "pullback_max_vol_ratio": 1.2,
    "min_score": 25.0,
    "pool_size": 50,
    "adjustflag": "2",
    "pool_workers": 0,  # 回测时并行评分进程数，0=不并行
}


def _score_one_stock(args: tuple) -> dict[str, Any] | None:
    """供多进程调用的单只股票评分，仅用可序列化参数，内部自行 load_kline"""
    global _bs_login_pid
    pid = os.getpid()
    if _bs_login_pid != pid:
        import baostock as bs
        bs.login()
        _bs_login_pid = pid

    code, name, date, benchmark_ret20, config = args
    from data.kline_loader import load_kline
    min_bars = config.get("min_bars", 160)
    adjustflag = config.get("adjustflag", "2")
    df = load_kline(code, date, lookback_days=500, adjustflag=adjustflag)
    if df is None or df.empty or len(df) < min_bars:
        return None
    if "code" not in df.columns:
        df["code"] = code
    scored = _score_one(df, benchmark_ret20, config)
    if scored:
        scored["name"] = name
    return scored


def _ma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(n).mean()
    return atr / close


def _score_one(df: pd.DataFrame, bench_ret20: float, config: dict) -> dict[str, Any] | None:
    cfg = config
    df = df.copy()
    df["ma5"] = _ma(df["close"], 5)
    df["ma20"] = _ma(df["close"], 20)
    df["ma60"] = _ma(df["close"], 60)
    df["ma120"] = _ma(df["close"], 120)
    df["vol_ma20"] = _ma(df["volume"], 20)
    df["avg_amt20"] = _ma(df["amount"], 20)
    df["atrp"] = _atr_pct(df, 14)
    df["ret20"] = df["close"].pct_change(20)

    d = df.dropna().iloc[-1]

    if float(d["avg_amt20"]) < cfg["min_avg_amount_20"]:
        return None

    ma20_up = df["ma20"].iloc[-1] > df["ma20"].iloc[-6]
    if not (d["ma5"] > d["ma20"] > d["ma60"] > d["ma120"] and ma20_up):
        return None

    n_above = cfg["min_days_above_ma120"]
    recent_above = df.iloc[-n_above:]
    if not (recent_above["close"] > recent_above["ma120"]).all():
        return None

    n_trend = cfg["min_trend_days"]
    recent_trend = df.iloc[-n_trend:]
    if not (recent_trend["ma20"] > recent_trend["ma60"]).all():
        return None

    close = float(d["close"])
    ma20v = float(d["ma20"])
    if close / ma20v > cfg["max_close_over_ma20"]:
        return None

    prev20_max_close = df["close"].shift(1).rolling(20).max().iloc[-1]
    vol_ratio = float(d["volume"]) / float(d["vol_ma20"]) if float(d["vol_ma20"]) > 0 else 0
    is_breakout = (close >= float(prev20_max_close)) and (vol_ratio >= cfg["breakout_vol_ratio"])

    tol = cfg["pullback_touch_tol"]
    recent = df.iloc[-6:-1]
    touched = (
        (recent["low"] <= recent["ma20"] * (1 + tol))
        & (recent["low"] >= recent["ma20"] * (1 - tol))
    ).any()
    is_pullback = touched and (close > ma20v) and (vol_ratio <= cfg["pullback_max_vol_ratio"])

    if not (is_breakout or is_pullback):
        return None

    rs20 = float(d["ret20"]) - float(bench_ret20)
    if rs20 < cfg["min_rs20"]:
        return None
    trend_open = float(d["ma20"] / d["ma60"] - 1) + float(d["ma60"] / d["ma120"] - 1)
    risk = float(d["atrp"])

    score = (
        80 * rs20
        + 120 * trend_open
        + 8 * math.log(max(vol_ratio, 1e-3))
        - 60 * risk
        + (8 if is_breakout else 0)
        + (4 if is_pullback else 0)
    )

    min_score = cfg.get("min_score")
    if min_score is not None and score < min_score:
        return None

    setup = ("breakout" if is_breakout else "") + (
        "+pullback" if (is_breakout and is_pullback) else ("pullback" if is_pullback else "")
    )

    return {
        "code": df["code"].iloc[-1] if "code" in df.columns else "",
        "close": close,
        "vol_ratio": round(vol_ratio, 2),
        "avg_amt20": float(d["avg_amt20"]),
        "rs20": rs20,
        "trend_open": trend_open,
        "atrp": risk,
        "score": score,
        "setup": setup,
    }


class TrendMAStrategy(BaseStrategy):
    """趋势均线选股策略"""

    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    def screen(
        self,
        date: str,
        universe_df: pd.DataFrame,
        get_kline_fn: callable,
        benchmark_ret20: float,
    ) -> list[dict[str, Any]]:
        min_bars = self.config.get("min_bars", 160)
        pool_size = self.config.get("pool_size", 50)
        n_workers = int(self.config.get("pool_workers") or 0)

        if n_workers > 1:
            args_list = [
                (r["code"], r["code_name"], date, benchmark_ret20, self.config)
                for _, r in universe_df.iterrows()
            ]
            with Pool(processes=n_workers) as pool:
                results = pool.map(_score_one_stock, args_list)
            rows = [r for r in results if r is not None]
        else:
            rows = []
            for _, r in universe_df.iterrows():
                code, name = r["code"], r["code_name"]
                df = get_kline_fn(code, date)
                if df is None or df.empty or len(df) < min_bars:
                    continue
                if "code" not in df.columns:
                    df["code"] = code
                scored = _score_one(df, benchmark_ret20, self.config)
                if scored:
                    scored["name"] = name
                    rows.append(scored)

        out = pd.DataFrame(rows)
        if out.empty:
            return []
        out = out.sort_values("score", ascending=False).drop_duplicates(subset=["code"], keep="first")
        return out.head(pool_size).to_dict("records")

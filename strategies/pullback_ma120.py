"""
120MA 回踩选股策略
条件：股价 > 120MA 连续 60 天，回调到 120MA 企稳，量能缩量，60MA > 120MA
"""

import os
from multiprocessing import Pool
from typing import Any

import pandas as pd

from .base import BaseStrategy

_bs_login_pid = None

DEFAULT_CONFIG = {
    "min_bars": 180,
    "min_avg_amount_20": 5e7,
    "min_days_above_ma120": 60,
    "min_ma60_above_ma120_days": 30,
    "pullback_lookback": 5,
    "pullback_touch_tol": 0.01,
    "max_close_over_ma120": 1.05,
    "max_breakdown_below_ma120": 0.03,  # 回踩期内最低价不得跌破 120MA 超过此比例（旧逻辑）
    "pullback_low_min_ma120": 0.98,   # 最近 N 天收盘价 >= MA120 * 此值（回踩在线上方）
    "pullback_low_max_ma120": 1.02,   # 最近 N 天最低价 <= MA120 * 此值（不破线过深）
    "min_ma120_rise_60d": 0.03,  # 当日 120MA 比 60 天前高至少此比例（如 3%），确保长均线上升
    "volume_shrink_ratio": 0.85,
    "min_rs20": 0.0,
    "pool_size": 50,
    "adjustflag": "2",
    "pool_workers": 0,
}


def _score_one_stock(args: tuple) -> dict[str, Any] | None:
    global _bs_login_pid
    pid = os.getpid()
    if _bs_login_pid != pid:
        import baostock as bs
        bs.login()
        _bs_login_pid = pid

    code, name, date, benchmark_ret20, config = args
    from data.kline_loader import load_kline
    min_bars = config.get("min_bars", 180)
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
    df["ma60"] = _ma(df["close"], 60)
    df["ma120"] = _ma(df["close"], 120)
    df["vol_ma5"] = _ma(df["volume"], 5)
    df["vol_ma20"] = _ma(df["volume"], 20)
    df["avg_amt20"] = _ma(df["amount"], 20)
    df["atrp"] = _atr_pct(df, 14)
    df["ret20"] = df["close"].pct_change(20)

    d = df.dropna().iloc[-1]
    if float(d["avg_amt20"]) < cfg["min_avg_amount_20"]:
        return None

    # 回踩前：在进入回踩前，有足够天数收盘在 120MA 上方以确立趋势
    n_above = cfg["min_days_above_ma120"]
    lookback = cfg["pullback_lookback"]
    if n_above > lookback:
        before_pullback = df.iloc[-n_above:-lookback]
        if len(before_pullback) > 0 and not (before_pullback["close"] > before_pullback["ma120"]).all():
            return None

    n_ma_trend = cfg["min_ma60_above_ma120_days"]
    recent_ma = df.iloc[-n_ma_trend:]
    if not (recent_ma["ma60"] > recent_ma["ma120"]).all():
        return None

    # 当日 120MA 比 60 天前高至少 min_ma120_rise_60d（如 3%），确保长均线上升
    if len(df) >= 61:
        ma120_today = float(d["ma120"])
        ma120_60d_ago = float(df["ma120"].iloc[-61])
        if ma120_60d_ago <= 0:
            return None
        min_rise = cfg.get("min_ma120_rise_60d", 0.03)
        if ma120_today < ma120_60d_ago * (1 + min_rise):
            return None

    # 回踩区：支持两种参数（与 gm 版一致时用 pullback_low_min_ma120 / pullback_low_max_ma120）
    lookback = cfg["pullback_lookback"]
    recent = df.iloc[-lookback:]
    low_min = cfg.get("pullback_low_min_ma120")
    low_max = cfg.get("pullback_low_max_ma120")
    if low_min is not None and low_max is not None:
        # 与 gm 一致：最近 N 天 最低价 <= MA120*low_max，且 收盘价 >= MA120*low_min
        low_ok = recent["low"] <= recent["ma120"] * low_max
        close_ok = recent["close"] >= recent["ma120"] * low_min
        if not (low_ok & close_ok).all():
            return None
    else:
        # 旧逻辑：tol / max_over / max_breakdown
        tol = cfg["pullback_touch_tol"]
        max_over = cfg["max_close_over_ma120"]
        max_breakdown = cfg.get("max_breakdown_below_ma120", 0.03)
        in_pullback_zone = (
            (recent["close"] >= recent["ma120"] * (1 - tol))
            & (recent["close"] <= recent["ma120"] * max_over)
        )
        if not in_pullback_zone.all():
            return None
        if (recent["low"] < recent["ma120"] * (1 - max_breakdown)).any():
            return None

    close = float(d["close"])
    ma120v = float(d["ma120"])
    if low_min is None or low_max is None:
        tol = cfg["pullback_touch_tol"]
        max_over = cfg["max_close_over_ma120"]
        if close <= ma120v * (1 - tol) or close / ma120v > max_over:
            return None

    vol5 = float(d["vol_ma5"]) if d["vol_ma5"] and float(d["vol_ma5"]) > 0 else 0
    vol20 = float(d["vol_ma20"]) if d["vol_ma20"] and float(d["vol_ma20"]) > 0 else 1
    vol_ratio = vol5 / vol20 if vol20 > 0 else 0
    if vol_ratio > cfg["volume_shrink_ratio"]:
        return None

    rs20 = float(d["ret20"]) - float(bench_ret20)
    if rs20 < cfg["min_rs20"]:
        return None

    ma60v = float(d["ma60"])
    trend_score = (ma60v / ma120v - 1) * 100
    shrink_score = (1 - vol_ratio) * 50
    rs_score = rs20 * 80
    score = trend_score + shrink_score + rs_score

    return {
        "code": df["code"].iloc[-1] if "code" in df.columns else "",
        "close": close,
        "vol_ratio": round(vol_ratio, 2),
        "avg_amt20": float(d["avg_amt20"]),
        "rs20": rs20,
        "atrp": float(d["atrp"]),
        "score": score,
        "setup": "pullback_ma120",
    }


class PullbackMA120Strategy(BaseStrategy):
    """120MA 回踩选股策略：股价 > 120MA 60 天，回踩企稳，缩量，60MA > 120MA"""

    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    def screen(
        self,
        date: str,
        universe_df: pd.DataFrame,
        get_kline_fn: callable,
        benchmark_ret20: float,
    ) -> list[dict[str, Any]]:
        min_bars = self.config.get("min_bars", 180)
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
        out = out.sort_values("score", ascending=False).drop_duplicates(
            subset=["code"], keep="first"
        )
        return out.head(pool_size).to_dict("records")

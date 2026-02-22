# -*- coding: utf-8 -*-
"""
掘金版：回踩120均线策略
与 strategies/pullback_ma120.py 及 bt_config_pullback_ma120.yaml 逻辑一致。
仅依赖 gm（掘金 SDK）和 pandas，不依赖本仓库的 baostock/akshare/data.*。

Token：将 your_token_here 改为你的 token，或设置环境变量 GM_TOKEN。
运行：python gm_strategy/gm_pullback_ma120.py（需 pip install gm）
"""

import os
from typing import Any

import pandas as pd

# 掘金 SDK：需 pip install gm
try:
    from gm.api import (
        ADJUST_PREV,
        get_history_constituents,
        get_previous_trading_date,
        get_trading_dates,
        history,
        history_n,
        run,
        schedule,
        order_volume,
        order_value,
        PositionSide_Long,
        PositionEffect_Close,
        PositionEffect_Open,
        OrderType_Market,
        OrderSide_Buy,
        OrderSide_Sell,
    )
except ImportError:
    raise ImportError("请安装掘金 SDK: pip install gm")
try:
    from gm.api import log as _gm_log
except Exception:
    _gm_log = None

def _log(msg: str) -> None:
    """优先使用掘金 log，否则 print，便于在终端与本地都能看到。"""
    if _gm_log is not None:
        try:
            _gm_log(msg)
        except Exception:
            print(msg)
    else:
        print(msg)

GM_TOKEN = os.environ.get("GM_TOKEN", "your_token_here")
EXCHANGE = "SHSE"  # 用于 get_previous_trading_date 等

# ---------- 配置（与 bt_config_pullback_ma120.yaml 对齐）----------
DEFAULT_CONFIG = {
    "min_bars": 180,
    "min_avg_amount_20": 5e7,
    "min_days_above_ma120": 60,
    "min_ma60_above_ma120_days": 30,
    "pullback_lookback": 5,
    "pullback_touch_tol": 0.02,
    "max_close_over_ma120": 1.05,
    "max_breakdown_below_ma120": 0.03,
    "min_ma120_rise_60d": 0.03,
    "volume_shrink_ratio": 0.85,
    "min_rs20": 0.0,
    "pool_size": 50,
    "universe_index": "SHSE.000905",   # 中证500（选股宇宙）
    "benchmark_symbol": "SHSE.000905",  # 与宇宙一致，用于相对强度
    "max_positions": 5,
    "initial_capital": 500000,
    "stop_loss_pct": -0.05,
    "take_profit_pct": 0.30,
    "break_ma20_pct": 0.02,
    "hold_days": 3,
    "hold_strong_pct": 0.03,
    "backtest_start": "2023-01-01",
    "backtest_end": "2023-08-31",
}


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


def _trading_dates_between(exchange: str, start_date: str, end_date: str) -> list[str]:
    """获取 [start_date, end_date] 之间的交易日列表（含端点）。若 get_trading_dates 不可用则用 get_previous_trading_date 循环。"""
    try:
        out = get_trading_dates(exchange, start_date, end_date)
        if out is not None and len(out) > 0:
            return list(out) if not isinstance(out[0], str) else out
    except Exception:
        pass
    result = []
    d = end_date
    while d >= start_date:
        result.append(d)
        try:
            d = get_previous_trading_date(exchange, d)
        except Exception:
            break
    result.reverse()
    return result


def get_gm_constituents(index_symbol: str, date: str, df: bool = True):
    """当日指数成分股。掘金: get_history_constituents(index, start_date, end_date, df=True)。"""
    try:
        ret = get_history_constituents(
            index=index_symbol,
            start_date=date,
            end_date=date,
            df=df,
        )
        if df and ret is not None and not ret.empty and "symbol" in ret.columns:
            return ret["symbol"].tolist()
        if not df and ret:
            return [r.get("symbol") for r in ret if r.get("symbol")]
        return []
    except Exception:
        return []


def get_benchmark_ret20(benchmark_symbol: str, end_date: str, adjust: int = ADJUST_PREV) -> float | None:
    """基准 end_date 对应的 20 日收益率（用 end_date 及之前日线算 pct_change(20)）。"""
    try:
        # end_date 当天 15:00 后取到的日线已含当日，用 end_date 作为 end_time
        df = history(
            benchmark_symbol,
            frequency="1d",
            start_time=(pd.to_datetime(end_date) - pd.Timedelta(days=60)).strftime("%Y-%m-%d 00:00:00"),
            end_time=end_date + " 15:00:00",
            adjust=adjust,
            df=True,
        )
        if df is None or df.empty or len(df) < 21:
            return None
        if "eob" in df.columns:
            df["date"] = pd.to_datetime(df["eob"]).dt.strftime("%Y-%m-%d")
        else:
            return None
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        close = df["close"]
        ret20 = close.pct_change(20)
        # 最后一行对应 end_date 的 20 日收益
        if len(ret20) > 0 and pd.notna(ret20.iloc[-1]):
            return float(ret20.iloc[-1])
        return None
    except Exception:
        return None


def gm_bar_to_df(gm_df: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    """将掘金 history 返回的 DataFrame（含 eob）转为与 _score_one 一致的列名，并增加 date 列。"""
    if gm_df is None or gm_df.empty:
        return None
    df = gm_df.copy()
    if "eob" in df.columns:
        df["date"] = pd.to_datetime(df["eob"]).dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col not in df.columns:
            return None
    df["code"] = symbol
    return df


def score_one_gm(
    symbol: str,
    df_bar: pd.DataFrame,
    bench_ret20: float,
    config: dict,
) -> dict[str, Any] | None:
    """
    与 strategies/pullback_ma120._score_one 逻辑一致。
    df_bar 需含列: open, high, low, close, volume, amount, date（或 eob 转成的 date）, code.
    """
    cfg = config
    min_bars = cfg.get("min_bars", 180)
    if df_bar is None or len(df_bar) < min_bars:
        return None
    df = df_bar.copy()
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
    if len(df) >= 61:
        ma120_today = float(d["ma120"])
        ma120_60d_ago = float(df["ma120"].iloc[-61])
        if ma120_60d_ago <= 0:
            return None
        min_rise = cfg.get("min_ma120_rise_60d", 0.03)
        if ma120_today < ma120_60d_ago * (1 + min_rise):
            return None
    lookback = cfg["pullback_lookback"]
    tol = cfg["pullback_touch_tol"]
    max_over = cfg["max_close_over_ma120"]
    max_breakdown = cfg.get("max_breakdown_below_ma120", 0.03)
    recent = df.iloc[-lookback:]
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
        "code": symbol,
        "close": close,
        "vol_ratio": round(vol_ratio, 2),
        "avg_amt20": float(d["avg_amt20"]),
        "rs20": rs20,
        "atrp": float(d["atrp"]),
        "score": score,
        "setup": "pullback_ma120",
    }


def _get_ma20_from_bars(df: pd.DataFrame) -> float | None:
    """从日线 DataFrame 计算最后一日的 MA20。"""
    if df is None or len(df) < 20:
        return None
    tail = df.tail(21)
    ma20 = float(tail["close"].rolling(20).mean().iloc[-1])
    return ma20 if ma20 > 0 else None


def _exit_reason(
    buy_price: float,
    high_curr: float,
    low_curr: float,
    stop_loss: float | None,
    take_profit: float | None,
    ma20_sell_price: float | None,
) -> str:
    """与 backtest._exit_price_with_stop_target 一致，仅返回退出原因。"""
    stop_price = buy_price * (1.0 + stop_loss) if stop_loss is not None else None
    target_price = buy_price * (1.0 + take_profit) if take_profit is not None else None
    below_candidates = []
    if stop_price is not None:
        below_candidates.append((stop_price, "stop_loss"))
    if ma20_sell_price is not None and ma20_sell_price < buy_price:
        below_candidates.append((ma20_sell_price, "break_ma20"))
    below_candidates.sort(key=lambda x: -x[0])
    for price, reason in below_candidates:
        if low_curr <= price:
            return reason
    if target_price is not None and high_curr >= target_price:
        return "take_profit"
    return "normal"


def init(context):
    context.config = DEFAULT_CONFIG.copy()
    context.universe_index = context.config["universe_index"]
    context.benchmark_symbol = context.config["benchmark_symbol"]
    context.next_buy_list = []
    context.positions_meta = {}
    schedule(schedule_func=algo, date_rule="1d", time_rule="09:35:00")
    _log(
        f"[init] 回踩120均线 宇宙={context.universe_index} 基准={context.benchmark_symbol} "
        f"max_positions={context.config['max_positions']} pool_size={context.config['pool_size']} "
        f"回测区间={context.config['backtest_start']}~{context.config['backtest_end']}"
    )


def algo(context):
    cur = context.now.strftime("%Y-%m-%d")
    try:
        prev = get_previous_trading_date(EXCHANGE, cur)
    except Exception:
        prev = (pd.to_datetime(cur) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    config = context.config
    max_positions = config["max_positions"]
    pool_size = config["pool_size"]
    adjust = ADJUST_PREV

    positions = context.account().positions(side=PositionSide_Long)
    held = {p.symbol for p in positions}

    if not hasattr(context, "positions_meta"):
        context.positions_meta = {}
    if not hasattr(context, "next_buy_list"):
        context.next_buy_list = []

    backtest_end = getattr(context, "backtest_end_time", None)
    if backtest_end:
        end_str = backtest_end[:10] if isinstance(backtest_end, str) else backtest_end.strftime("%Y-%m-%d")
        if cur >= end_str:
            n_close = len(positions)
            for p in positions:
                try:
                    order_volume(
                        symbol=p.symbol,
                        volume=p.volume,
                        side=OrderSide_Sell,
                        order_type=OrderType_Market,
                        position_effect=PositionEffect_Close,
                    )
                except Exception:
                    pass
            _log(f"[{cur}] 回测结束日 平仓 {n_close} 只")
            context.next_buy_list = []
            return

    cash = context.account().cash.nav
    _log(f"[{cur}] 昨日={prev} 持仓={len(positions)} 只 现金={cash:,.0f}")

    sell_reasons = []
    for p in positions:
        sym = p.symbol
        if sym not in context.positions_meta:
            context.positions_meta[sym] = {"buy_date": cur, "buy_price": p.vwap}
        buy_date = context.positions_meta[sym]["buy_date"]
        buy_price = context.positions_meta[sym].get("buy_price") or p.vwap
        if buy_date == cur:
            continue
        try:
            bars = history_n(
                symbol=sym,
                frequency="1d",
                count=30,
                end_time=cur + " 15:00:00",
                adjust=adjust,
                df=True,
                fields="open,high,low,close,volume,amount,eob",
            )
        except Exception:
            bars = None
        if bars is None or bars.empty:
            continue
        high_t = float(bars["high"].iloc[-1]) if "high" in bars.columns else None
        low_t = float(bars["low"].iloc[-1]) if "low" in bars.columns else None
        if high_t is None or low_t is None:
            continue

        hold_days_cfg = config.get("hold_days")
        hold_strong_pct = config.get("hold_strong_pct")
        if hold_days_cfg is not None and hold_strong_pct is not None:
            try:
                dates_list = _trading_dates_between(EXCHANGE, buy_date, cur)
                if buy_date in dates_list:
                    idx_buy = dates_list.index(buy_date)
                    held_days = len(dates_list) - 1 - idx_buy
                    if held_days >= hold_days_cfg:
                        highs = []
                        for j in range(1, hold_days_cfg + 1):
                            if idx_buy + j < len(dates_list):
                                d = dates_list[idx_buy + j]
                                b = history_n(sym, "1d", 5, end_time=d + " 15:00:00", adjust=adjust, df=True)
                                if b is not None and not b.empty and "high" in b.columns:
                                    highs.append(float(b["high"].iloc[-1]))
                        if len(highs) == hold_days_cfg and all(h is not None for h in highs):
                            threshold = buy_price * (1.0 + hold_strong_pct)
                            if max(highs) < threshold:
                                sell_reasons.append((sym, "weak_exit"))
                                try:
                                    order_volume(
                                        symbol=sym,
                                        volume=p.volume,
                                        side=OrderSide_Sell,
                                        order_type=OrderType_Market,
                                        position_effect=PositionEffect_Close,
                                    )
                                except Exception:
                                    pass
                                continue
            except Exception:
                pass

        stop_loss = config.get("stop_loss_pct")
        take_profit = config.get("take_profit_pct")
        break_ma20_pct = config.get("break_ma20_pct")
        ma20_sell_price = None
        if break_ma20_pct is not None:
            ma20 = _get_ma20_from_bars(bars)
            if ma20 is not None and ma20 > 0:
                ma20_sell_price = ma20 * (1.0 - break_ma20_pct)
        reason = _exit_reason(
            buy_price, high_t, low_t,
            stop_loss, take_profit, ma20_sell_price,
        )
        if reason != "normal":
            sell_reasons.append((sym, reason))
            try:
                order_volume(
                    symbol=sym,
                    volume=p.volume,
                    side=OrderSide_Sell,
                    order_type=OrderType_Market,
                    position_effect=PositionEffect_Close,
                )
            except Exception:
                pass

    if sell_reasons:
        _log(f"[{cur}] 卖出 {len(sell_reasons)} 只: " + ", ".join(f"{s}({r})" for s, r in sell_reasons))

    if context.next_buy_list and cash > 0:
        to_buy = [s for s in context.next_buy_list if s not in held][: max_positions - len(held)]
        if to_buy:
            per_value = cash / len(to_buy)
            _log(f"[{cur}] 买入 {len(to_buy)} 只 每只约{per_value:,.0f}元: {', '.join(to_buy)}")
            for sym in to_buy:
                try:
                    order_value(
                        symbol=sym,
                        value=per_value,
                        side=OrderSide_Buy,
                        order_type=OrderType_Market,
                        position_effect=PositionEffect_Open,
                    )
                except Exception:
                    pass
        context.next_buy_list = []

    symbols = get_gm_constituents(context.universe_index, prev, df=True)
    if not symbols:
        _log(f"[{cur}] 选股日{prev} 成分股为空，跳过")
        return
    bench_ret = get_benchmark_ret20(context.benchmark_symbol, prev, adjust)
    if bench_ret is None:
        bench_ret = 0.0
    scored_list = []
    for sym in symbols:
        try:
            df_bar = history_n(
                symbol=sym,
                frequency="1d",
                count=300,
                end_time=prev + " 15:00:00",
                adjust=adjust,
                df=True,
                fields="open,high,low,close,volume,amount,eob",
            )
        except Exception:
            continue
        if df_bar is None or df_bar.empty:
            continue
        df_bar = gm_bar_to_df(df_bar, sym)
        if df_bar is None:
            continue
        r = score_one_gm(sym, df_bar, bench_ret, config)
        if r:
            scored_list.append(r)
    if not scored_list:
        _log(f"[{cur}] 选股日{prev} 成分股{len(symbols)}只 入池0只")
        context.next_buy_list = []
        return
    out = pd.DataFrame(scored_list).sort_values("score", ascending=False).drop_duplicates(subset=["code"], keep="first")
    context.next_buy_list = out.head(pool_size)["code"].tolist()
    top5 = context.next_buy_list[:5]
    _log(f"[{cur}] 选股日{prev} 成分股{len(symbols)}只 入池{len(context.next_buy_list)}只 明日待买前5: {top5}")


if __name__ == "__main__":
    run(
        strategy_id="gm_pullback_ma120",
        filename="gm_pullback_ma120.py",
        token=GM_TOKEN,
        backtest_start_time=DEFAULT_CONFIG["backtest_start"] + " 09:00:00",
        backtest_end_time=DEFAULT_CONFIG["backtest_end"] + " 15:00:00",
        backtest_adjust=ADJUST_PREV,
        backtest_initial_cash=DEFAULT_CONFIG["initial_capital"],
        backtest_commission_ratio=0.0003,
        backtest_slippage_ratio=0.0001,
    )

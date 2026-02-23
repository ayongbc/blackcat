# -*- coding: utf-8 -*-
"""
掘金版：回踩120均线策略
与 strategies/pullback_ma120.py 及 bt_config_pullback_ma120.yaml 逻辑一致。
仅依赖 gm（掘金 SDK）和 pandas，不依赖本仓库的 baostock/akshare/data.*。

Token：将 your_token_here 改为你的 token，或设置环境变量 GM_TOKEN。
运行：python gm_strategy/gm_pullback_ma120.py（需 pip install gm）
"""

import os
from collections import Counter
from typing import Any

import pandas as pd

# 掘金 SDK：需 pip install gm
try:
    from gm.api import (
        ADJUST_PREV,
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
    try:
        from gm.api import stk_get_index_constituents
    except ImportError:
        stk_get_index_constituents = None
    try:
        from gm.api import get_constituents
    except ImportError:
        get_constituents = None
except ImportError:
    raise ImportError("请安装掘金 SDK: pip install gm")
try:
    from gm.api import ADJUST_NONE
except ImportError:
    ADJUST_NONE = 0  # 不复权
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

# 选股调试：True 时输出每个过滤条件的统计，便于排查“选不出股”
DEBUG_SELECTION = True

# ---------- 配置（与 bt_config_pullback_ma120.yaml 对齐）----------
DEFAULT_CONFIG = {
    "min_bars": 180,
    "min_avg_amount_20": 5e7,
    "min_days_above_ma120": 60,
    "min_ma60_above_ma120_days": 30,
    "pullback_lookback": 5,
    "pullback_low_min_ma120": 0.98,   # 最近 N 天最低价 >= MA120 * 此值（回踩不破线）
    "pullback_low_max_ma120": 1.02,   # 最近 N 天收盘价 > MA120 * 此值（收盘在线上方）
    "min_ma120_rise_60d": 0.03,
    "volume_shrink_ratio": 0.85,
    "min_rs20": 0.0,
    "pool_size": 50,
    "universe_index": "SHSE.000300",   # 中证500（选股宇宙）
    "benchmark_symbol": "SHSE.000300",  # 与宇宙一致，用于相对强度
    "max_positions": 3,
    "initial_capital": 500000,
    "stop_loss_pct": -0.05,
    "take_profit_pct": 0.30,
    "drawdown_from_high_pct": 0.10,   # 买入后高点回撤 10% 卖出
    "drawdown_min_rise_pct": 0.05,    # 仅当买入后曾涨过至少 5% 才启用回撤卖出，避免刚买没涨就误触
    "backtest_start": "2023-09-02",
    "backtest_end": "2023-12-19",
    "backtest_adjust": 1,   # 回测成交价/持仓成本：1=前复权(ADJUST_PREV)，0=不复权(ADJUST_NONE)。应与选股、卖出用同一复权
    "sell_bars_adjust": 1, # 卖出判断用K线：1=前复权，0=不复权。建议与 backtest_adjust 一致
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


def _parse_constituents_ret(ret, index_symbol: str, date: str):
    """从 API 返回值解析出 symbol 列表。支持 DataFrame 或 list of dict，列名 symbol/sec_id/code。"""
    if ret is None:
        return None
    # DataFrame
    if hasattr(ret, "columns") and hasattr(ret, "empty"):
        if ret.empty:
            return []
        for col in ("symbol", "sec_id", "code"):
            if col in ret.columns:
                syms = ret[col].dropna().astype(str).tolist()
                return syms
        if DEBUG_SELECTION:
            _log(f"[选股] 指数 {index_symbol} 日期 {date} 返回 DataFrame 但无 symbol 列，列名: {list(ret.columns)}")
        return []
    # list of dict
    if isinstance(ret, (list, tuple)):
        syms = []
        for r in ret:
            if not isinstance(r, dict):
                continue
            for key in ("symbol", "sec_id", "code"):
                if r.get(key):
                    syms.append(str(r[key]))
                    break
        return syms
    return None


def get_gm_constituents(index_symbol: str, date: str, df: bool = True):
    """当日指数成分股。优先用 stk_get_index_constituents / get_constituents（最新成分股），否则用 get_history_constituents（历史）。"""
    used_latest = False
    try:
        ret = None
        # 1) 新版：仅 index 参数，返回最新交易日成分股（回测时用“最新”近似当日）
        if stk_get_index_constituents is not None:
            try:
                ret = stk_get_index_constituents(index=index_symbol)
                used_latest = True
            except Exception:
                ret = None
        if (ret is None or (hasattr(ret, "empty") and ret.empty)) and get_constituents is not None:
            try:
                ret = get_constituents(index_symbol)
                used_latest = True
            except Exception:
                ret = None
        # 2) 旧版历史成分股（已弃用且可能返回空）
        if ret is None or (hasattr(ret, "empty") and ret.empty):
            from gm.api import get_history_constituents
            ret = get_history_constituents(
                index=index_symbol,
                start_date=date,
                end_date=date,
            )
            used_latest = False

        syms = _parse_constituents_ret(ret, index_symbol, date)
        if syms is None:
            if DEBUG_SELECTION:
                _log(f"[选股] 指数 {index_symbol} 日期 {date} 成分股为空 (ret=None)")
            return []
        if used_latest and DEBUG_SELECTION and len(syms) > 0:
            _log(f"[选股] 指数 {index_symbol} 使用「最新成分股」近似日期 {date}（共 {len(syms)} 只）")
        if DEBUG_SELECTION and len(syms) > 0:
            _log(f"[选股] 指数 {index_symbol} 日期 {date} 成分股数量: {len(syms)} 只")
        if len(syms) == 0 and DEBUG_SELECTION:
            _log(f"[选股] 指数 {index_symbol} 日期 {date} 成分股为空 (ret type={type(ret).__name__})")
        return syms
    except Exception as e:
        if DEBUG_SELECTION:
            _log(f"[选股] 获取成分股异常 index={index_symbol} date={date}: {e}")
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
) -> tuple[dict[str, Any] | None, str | None]:
    """
    与 strategies/pullback_ma120._score_one 逻辑一致。
    返回 (结果字典, None) 或 (None, 失败原因)，便于统计选股过滤原因。
    """
    cfg = config
    min_bars = cfg.get("min_bars", 180)
    if df_bar is None or len(df_bar) < min_bars:
        return None, f"K线不足(min_bars={min_bars}, 实际={len(df_bar) if df_bar is not None else 0})"
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
        return None, f"20日均额不足(需>={cfg['min_avg_amount_20']/1e7:.0f}百万, 实际={float(d['avg_amt20'])/1e7:.1f})"
    n_above = cfg["min_days_above_ma120"]
    lookback = cfg["pullback_lookback"]
    if n_above > lookback:
        before_pullback = df.iloc[-n_above:-lookback]
        if len(before_pullback) > 0 and not (before_pullback["close"] > before_pullback["ma120"]).all():
            return None, "回踩前未持续在120线上方"
    n_ma_trend = cfg["min_ma60_above_ma120_days"]
    recent_ma = df.iloc[-n_ma_trend:]
    if not (recent_ma["ma60"] > recent_ma["ma120"]).all():
        return None, "近期ma60未持续>ma120"
    if len(df) >= 61:
        ma120_today = float(d["ma120"])
        ma120_60d_ago = float(df["ma120"].iloc[-61])
        if ma120_60d_ago <= 0:
            return None, "60日前ma120无效"
        min_rise = cfg.get("min_ma120_rise_60d", 0.03)
        if ma120_today < ma120_60d_ago * (1 + min_rise):
            return None, f"120线60日涨幅不足(需>={min_rise*100:.0f}%)"
    lookback = cfg["pullback_lookback"]
    low_min = cfg.get("pullback_low_min_ma120", 0.98)
    low_max = cfg.get("pullback_low_max_ma120", 1.02)
    recent = df.iloc[-lookback:]
    # 回踩120：最近 N 天 最低价 >= MA120*low_min，且 收盘价 > MA120*low_max    
    low_ok = recent["low"] <= recent["ma120"] * low_max
    close_ok = recent["close"] >= recent["ma120"] * low_min
    if not (low_ok & close_ok).all():
        return None, f"近{lookback}日未同时满足: 最低价>={low_min}*MA120 且 收盘>{low_max}*MA120"
    close = float(d["close"])
    ma120v = float(d["ma120"])
    vol5 = float(d["vol_ma5"]) if d["vol_ma5"] and float(d["vol_ma5"]) > 0 else 0
    vol20 = float(d["vol_ma20"]) if d["vol_ma20"] and float(d["vol_ma20"]) > 0 else 1
    vol_ratio = vol5 / vol20 if vol20 > 0 else 0
    if vol_ratio > cfg["volume_shrink_ratio"]:
        return None, f"量比过大(vol5/vol20={vol_ratio:.2f}> {cfg['volume_shrink_ratio']})"
    rs20 = float(d["ret20"]) - float(bench_ret20)
    if rs20 < cfg["min_rs20"]:
        return None, f"相对强度不足(rs20={rs20:.3f}< {cfg['min_rs20']})"
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
    }, None


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
    close_curr: float,
    stop_loss: float | None,
    take_profit: float | None,
    ma20_sell_price: float | None,
    break_ma20_use_close: bool = True,
) -> str:
    """
    退出原因判断。
    止损/止盈：用当日最低/最高价判断（盘中触及即触发）。
    跌破 MA20：默认用收盘价判断，避免盘中下影线触及就卖（break_ma20_use_close=True）。
    """
    stop_price = buy_price * (1.0 + stop_loss) if stop_loss is not None else None
    target_price = buy_price * (1.0 + take_profit) if take_profit is not None else None
    if stop_price is not None and low_curr <= stop_price:
        return "stop_loss"
    if target_price is not None and high_curr >= target_price:
        return "take_profit"
    if ma20_sell_price is not None and ma20_sell_price < buy_price:
        if break_ma20_use_close:
            if close_curr <= ma20_sell_price:
                return "break_ma20"
        else:
            if low_curr <= ma20_sell_price:
                return "break_ma20"
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
    # 与 backtest_adjust 一致：选股/基准用同一复权
    adjust = ADJUST_NONE if config.get("backtest_adjust", 1) == 0 else ADJUST_PREV

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
    # 总资产（净值）：掘金 account 的 nav 为总资产，若无则用现金+持仓市值近似
    total_nav = getattr(context.account(), "nav", None)
    if total_nav is None and hasattr(context.account(), "cash"):
        total_nav = cash + sum(getattr(p, "market_value", 0) or (getattr(p, "volume", 0) * getattr(p, "vwap", 0)) for p in positions)
    if total_nav is None:
        total_nav = cash
    _log(f"[{cur}] 昨日={prev} 持仓={len(positions)} 只 现金={cash:,.0f} 总资产={total_nav:,.0f}")

    # ---------- 卖出逻辑（满足任一即卖）----------
    # 1) stop_loss: 当日最低价 <= 买入价*(1+stop_loss_pct)，如 -5% 止损
    # 2) take_profit: 当日最高价 >= 买入价*(1+take_profit_pct)，如 +30% 止盈
    # 3) drawdown_from_high: 当日收盘价 <= 买入后最高价*(1-drawdown_from_high_pct)，如回撤 10% 卖
    sell_reasons = []
    for p in positions:
        sym = p.symbol
        if sym not in context.positions_meta:
            context.positions_meta[sym] = {"buy_date": cur, "buy_price": p.vwap, "high_since_buy": p.vwap}
        buy_date = context.positions_meta[sym]["buy_date"]
        buy_price = context.positions_meta[sym].get("buy_price") or p.vwap
        if buy_date == cur:
            continue
        adjust_sell = ADJUST_NONE if config.get("sell_bars_adjust", 1) == 0 else ADJUST_PREV
        try:
            bars = history_n(
                symbol=sym,
                frequency="1d",
                count=30,
                end_time=cur + " 15:00:00",
                adjust=adjust_sell,
                df=True,
                fields="open,high,low,close,volume,amount,eob",
            )
        except Exception:
            bars = None
        if bars is None or bars.empty:
            continue
        # 按 eob 取「最新一根」K 线做卖出判断，避免 API 顺序导致取错日期
        if "eob" in bars.columns:
            bars = bars.copy()
            bars["_eob_dt"] = pd.to_datetime(bars["eob"])
            bars = bars.sort_values("_eob_dt", ascending=False).reset_index(drop=True)
            row = bars.iloc[0]
            bar_date = row["_eob_dt"].strftime("%Y-%m-%d") if hasattr(row["_eob_dt"], "strftime") else pd.Timestamp(row["_eob_dt"]).strftime("%Y-%m-%d")
            high_t = float(row["high"]) if "high" in row.index else None
            low_t = float(row["low"]) if "low" in row.index else None
            close_t = float(row["close"]) if "close" in row.index else None
        else:
            bar_date = cur
            high_t = float(bars["high"].iloc[-1]) if "high" in bars.columns else None
            low_t = float(bars["low"].iloc[-1]) if "low" in bars.columns else None
            close_t = float(bars["close"].iloc[-1]) if "close" in bars.columns else None
        if high_t is None or low_t is None or close_t is None:
            continue

        meta = context.positions_meta[sym]
        # 回撤判断用「昨日及之前」的最高价，不含当日最高；且仅当曾涨过 drawdown_min_rise_pct 才启用
        high_since_buy = meta.get("high_since_buy", buy_price)
        drawdown_pct = config.get("drawdown_from_high_pct")
        min_rise = config.get("drawdown_min_rise_pct")  # 例如 0.05：高点至少比成本高 5% 才看回撤
        high_enough = min_rise is None or high_since_buy >= buy_price * (1.0 + min_rise)
        if drawdown_pct is not None and high_enough and high_since_buy > 0 and close_t <= high_since_buy * (1.0 - drawdown_pct):
            dd_pct = (1.0 - close_t / high_since_buy) * 100
            _log(f"[{cur}] 卖出 {sym} 原因=高点回撤 用K线日期={bar_date} 复权={'不复权' if adjust_sell == ADJUST_NONE else '前复权'} 买入价={buy_price:.2f} 高点={high_since_buy:.2f} 收盘={close_t:.2f} 回撤={dd_pct:.1f}%")
            sell_reasons.append((sym, "drawdown_from_high"))
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

        # 更新买入后最高价（含当日），供下一日回撤判断用
        meta["high_since_buy"] = max(meta.get("high_since_buy", buy_price), high_t)

        stop_loss = config.get("stop_loss_pct")
        take_profit = config.get("take_profit_pct")
        reason = _exit_reason(
            buy_price, high_t, low_t, close_t,
            stop_loss, take_profit, None,
            break_ma20_use_close=True,
        )
        if reason != "normal":
            adjust_label = "不复权" if adjust_sell == ADJUST_NONE else "前复权"
            if reason == "stop_loss":
                _log(f"[{cur}] 卖出 {sym} 原因=止损 用K线日期={bar_date} 复权={adjust_label} 高={high_t:.2f} 低={low_t:.2f} 收={close_t:.2f} 买入价={buy_price:.2f} 止损线={buy_price * (1.0 + stop_loss):.2f}")
            elif reason == "take_profit":
                _log(f"[{cur}] 卖出 {sym} 原因=止盈 用K线日期={bar_date} 复权={adjust_label} 高={high_t:.2f} 低={low_t:.2f} 收={close_t:.2f} 买入价={buy_price:.2f} 止盈线={buy_price * (1.0 + take_profit):.2f}")
            else:
                _log(f"[{cur}] 卖出 {sym} 原因={reason} 用K线日期={bar_date} 复权={adjust_label}")
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
        _log(f"[{cur}] 卖出汇总 {len(sell_reasons)} 只: " + ", ".join(f"{s}({r})" for s, r in sell_reasons))

    if context.next_buy_list and cash > 0:
        to_buy = [s for s in context.next_buy_list if s not in held][: max_positions - len(held)]
        if to_buy:
            # 每只仓位 = 账户总资产 / max_positions（等权）
            per_value = total_nav / max_positions
            _log(f"[{cur}] 买入 {len(to_buy)} 只 每只约{per_value:,.0f}元(总资产/{max_positions}): {', '.join(to_buy)}")
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
    _log(f"[{cur}] 选股开始 选股日={prev} 成分股数量={len(symbols)}")

    bench_ret = get_benchmark_ret20(context.benchmark_symbol, prev, adjust)
    if bench_ret is None:
        bench_ret = 0.0
    if DEBUG_SELECTION:
        _log(f"[{cur}] 基准{context.benchmark_symbol} 20日收益率={bench_ret:.4f}")

    scored_list = []
    fail_reasons = Counter()
    bars_ok = 0
    bars_fail = 0
    for i, sym in enumerate(symbols):
        if DEBUG_SELECTION and (i + 1) % 100 == 0:
            _log(f"[{cur}] 选股进度 {i + 1}/{len(symbols)}")
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
        except Exception as e:
            bars_fail += 1
            if DEBUG_SELECTION and bars_fail <= 3:
                _log(f"[选股] 获取日线异常 {sym}: {e}")
            continue
        if df_bar is None or df_bar.empty:
            bars_fail += 1
            continue
        df_bar = gm_bar_to_df(df_bar, sym)
        if df_bar is None:
            bars_fail += 1
            continue
        bars_ok += 1
        r, reason = score_one_gm(sym, df_bar, bench_ret, config)
        if r:
            scored_list.append(r)
        elif reason:
            fail_reasons[reason] += 1

    if DEBUG_SELECTION:
        _log(f"[{cur}] 选股统计: 成分股={len(symbols)} 获取日线成功={bars_ok} 获取日线失败={bars_fail} 入池={len(scored_list)}")
        if fail_reasons:
            _log(f"[{cur}] 未通过原因统计(前10): " + ", ".join(f"{k}({v})" for k, v in fail_reasons.most_common(10)))

    if not scored_list:
        _log(f"[{cur}] 选股日{prev} 成分股{len(symbols)}只 入池0只 请根据上方「未通过原因统计」调整条件或日期")
        context.next_buy_list = []
        return
    out = pd.DataFrame(scored_list).sort_values("score", ascending=False).drop_duplicates(subset=["code"], keep="first")
    context.next_buy_list = out.head(pool_size)["code"].tolist()
    top5 = context.next_buy_list[:5]
    _log(f"[{cur}] 选股日{prev} 成分股{len(symbols)}只 入池{len(context.next_buy_list)}只 明日待买前5: {top5}")


if __name__ == "__main__":
    cfg = DEFAULT_CONFIG
    _run_adjust = ADJUST_NONE if cfg.get("backtest_adjust", 1) == 0 else ADJUST_PREV
    run(
        strategy_id="gm_pullback_ma120",
        filename="main.py",
        token='3ed29e8b09b04289ba1047ff089884a9a4117241',
        backtest_start_time=cfg["backtest_start"] + " 09:00:00",
        backtest_end_time=cfg["backtest_end"] + " 15:00:00",
        backtest_adjust=_run_adjust,
        backtest_initial_cash=cfg["initial_capital"],
        backtest_commission_ratio=0.0003,
        backtest_slippage_ratio=0.0001,
    )

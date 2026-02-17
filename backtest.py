#!/usr/bin/env python3
"""
回测引擎
低耦合设计：通过 config 指定策略和参数，策略实现 BaseStrategy 接口
"""

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
import baostock as bs

from data.kline_loader import load_kline
from data.universe import get_stock_list, get_trading_dates
from strategies import get_strategy


def _load_config(path: str) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            raise RuntimeError("YAML 配置需要 PyYAML: pip install pyyaml")
    return {}


def _compute_benchmark_ret20(benchmark: str, as_of_date: str, adjustflag: str = "2") -> float | None:
    df = load_kline(benchmark, as_of_date, lookback_days=60, adjustflag=adjustflag)
    if df.empty or len(df) < 40:
        return None
    df = df.copy()
    df["ret20"] = df["close"].pct_change(20)
    last = df.dropna().tail(1)
    if last.empty:
        return None
    return float(last["ret20"].iloc[-1])


def _get_price(price_cache: dict, code: str, date: str, field: str, load_fn) -> float | None:
    """从缓存或加载获取某日某字段"""
    key = (code, date, field)
    if key in price_cache:
        return price_cache[key]
    df = load_fn(code, date)
    if df is None or df.empty:
        price_cache[key] = None
        return None
    row = df[df["date"] == pd.to_datetime(date)]
    if row.empty:
        price_cache[key] = None
        return None
    val = float(row[field].iloc[0])
    price_cache[key] = val
    return val


def _get_ma20(price_cache: dict, code: str, date: str, load_fn) -> float | None:
    """获取标的在 date 当日的 MA20（收盘 20 日均），用于破线卖出判断"""
    key = (code, date, "ma20")
    if key in price_cache:
        return price_cache[key]
    df = load_fn(code, date)
    if df is None or df.empty or len(df) < 20:
        price_cache[key] = None
        return None
    df = df[df["date"] <= pd.to_datetime(date)].tail(21)
    if len(df) < 20:
        price_cache[key] = None
        return None
    ma20 = float(df["close"].rolling(20).mean().iloc[-1])
    price_cache[key] = ma20
    return ma20


def _exit_price_with_stop_target(
    entry: float,
    high_curr: float,
    low_curr: float,
    open_next: float,
    stop_loss: float | None,
    take_profit: float | None,
    ma20_sell_price: float | None,
) -> tuple[float, str, str]:
    """
    根据当日 high/low 判断是否触发止损/止盈/破20天线，返回 (退出价, 退出原因, 卖出日)。
    退出原因: normal | stop_loss | take_profit | break_ma20。
    卖出日: 当日触发出局则为 t_curr，否则为 t_next（由调用方传入）。
    """
    sell_date_next = ""  # 调用方用 t_next 填充
    if stop_loss is None and take_profit is None and ma20_sell_price is None:
        return (open_next, "normal", sell_date_next)
    stop_price = entry * (1.0 + stop_loss) if stop_loss is not None else None
    target_price = entry * (1.0 + take_profit) if take_profit is not None else None
    # 下方触发：止损、破 MA20，按离开盘近的优先（价高的先触到）
    below_candidates = []
    if stop_price is not None:
        below_candidates.append((stop_price, "stop_loss"))
    if ma20_sell_price is not None and ma20_sell_price < entry:
        below_candidates.append((ma20_sell_price, "break_ma20"))
    below_candidates.sort(key=lambda x: -x[0])  # 价格降序，先触到离 open 近的
    for price, reason in below_candidates:
        if low_curr <= price:
            return (price, reason, "t_curr")
    if target_price is not None and high_curr >= target_price:
        return (target_price, "take_profit", "t_curr")
    return (open_next, "normal", "t_next")


def run_backtest(config: dict) -> dict:
    """
    执行回测
    config 结构:
      strategy: 策略名 (如 trend_ma)
      strategy_params: 策略参数字典
      universe: 选股宇宙
      benchmark: 基准代码
      start_date: 回测起始
      end_date: 回测结束
      pool_size: 持仓数量
      hold_days: 持仓天数 (暂未用，采用每日调仓)
    """
    # 确保在调用任何 BaoStock 接口前已登录，避免 "you don't login"
    lg = bs.login()
    if lg.error_code != "0":
        return {"error": f"BaoStock 登录失败: {lg.error_msg}"}

    strategy_name = config.get("strategy", "trend_ma")
    strategy_params = config.get("strategy_params", {})
    universe = config.get("universe", "zz500")
    benchmark = config.get("benchmark", "sh.000852")
    start_date = config.get("start_date", "2023-01-01")
    end_date = config.get("end_date", dt.date.today().isoformat())
    end_date = str(end_date).strip() or dt.date.today().isoformat()
    pool_screen_start = config.get("pool_screen_start")
    pool_screen_end = config.get("pool_screen_end")
    if pool_screen_start is None or pool_screen_start == "" or str(pool_screen_start).lower() == "null":
        pool_screen_start = start_date
    else:
        pool_screen_start = str(pool_screen_start)
    if pool_screen_end is None or pool_screen_end == "" or str(pool_screen_end).lower() == "null":
        pool_screen_end = end_date
    else:
        pool_screen_end = str(pool_screen_end)
    pool_size = config.get("pool_size", 20)
    adjustflag = config.get("adjustflag", "2")
    allow_bj = config.get("allow_bj", False)
    # 止盈止损与破线：配置为点数
    _sl = config.get("stop_loss_pct")
    _tp = config.get("take_profit_pct")
    _bm = config.get("break_ma20_pct")
    stop_loss = (_sl / 100.0) if _sl is not None and float(_sl) != 0 else None
    take_profit = (_tp / 100.0) if _tp is not None and float(_tp) != 0 else None
    break_ma20_pct = (_bm / 100.0) if _bm is not None and float(_bm) != 0 else None
    hold_days = config.get("hold_days")
    _hsp = config.get("hold_strong_pct")
    hold_strong_pct = (_hsp / 100.0) if _hsp is not None and float(_hsp) != 0 else None
    initial_capital = float(config.get("initial_capital", 500000))
    max_positions = int(config.get("max_positions", 5))
    if max_positions < 1:
        max_positions = 1

    strategy_params["pool_size"] = strategy_params.get("pool_size", pool_size)
    strategy_cls = get_strategy(strategy_name)
    strategy = strategy_cls(config=strategy_params)

    def get_kline_fn(code: str, end_date: str):
        return load_kline(code, end_date, lookback_days=500, adjustflag=adjustflag)

    dates = get_trading_dates(start_date, end_date)
    if len(dates) < 3:
        return {"error": "交易日不足", "dates": dates}

    total_days = len(dates) - 2
    n_workers = int(strategy_params.get("pool_workers") or 0)
    print(f"  回测区间: {start_date} ~ {end_date}，共 {total_days} 个交易日", flush=True)
    print(f"  选股时间段: {pool_screen_start} ~ {pool_screen_end}（仅此区间内每日重选池，之后沿用最后池子）", flush=True)
    if n_workers > 1:
        print(f"  多进程加速: {n_workers} 个进程并行评分", flush=True)
    if stop_loss is not None or take_profit is not None or break_ma20_pct is not None:
        parts = []
        if stop_loss is not None:
            parts.append(f"止损 {config.get('stop_loss_pct')}%")
        if take_profit is not None:
            parts.append(f"止盈 {config.get('take_profit_pct')}%")
        if break_ma20_pct is not None:
            parts.append(f"破MA20 {config.get('break_ma20_pct')}%卖出")
        print(f"  止盈止损: {', '.join(parts)}", flush=True)
    if hold_days is not None and hold_strong_pct is not None:
        print(f"  弱势调仓: 持有 {hold_days} 日内未过买入价+{config.get('hold_strong_pct')}% 则调出", flush=True)
    print(f"  初始资金: {initial_capital:,.0f} 元，最多持仓: {max_positions} 只", flush=True)
    print("  规则: T+1（买入日不卖），选股次日开盘买，持有至止盈/止损/破MA20 触发才卖", flush=True)
    print("  开始循环...", flush=True)

    price_cache = {}
    # 持仓: [{code, name, buy_date, buy_price, position_size}]，position_size 为买入时占用资金（元）
    positions = []
    cash = initial_capital
    all_trades = []
    daily_returns = []
    pool_history = []
    value_prev = initial_capital
    log_every = max(1, total_days // 20)
    last_pool = None

    for i in range(1, len(dates) - 1):
        t_prev = dates[i - 1]
        t = dates[i]  # 当前日
        day_idx = i - 1

        bench_ret = _compute_benchmark_ret20(benchmark, t_prev, adjustflag)
        if bench_ret is None:
            daily_returns.append(0.0)
            pool_history.append({"date": t, "pool": [], "ret": 0.0, "trades": []})
            if day_idx % log_every == 0:
                print(f"  [{day_idx + 1}/{total_days}] {t} 基准无数据，跳过", flush=True)
            continue

        in_screen_window = pool_screen_start <= t_prev <= pool_screen_end
        if in_screen_window:
            uni_df = get_stock_list(universe, query_date=t_prev, allow_bj=allow_bj, as_of_date=t_prev)
            pool = strategy.screen(t_prev, uni_df, get_kline_fn, bench_ret)
            if pool:
                last_pool = pool
        else:
            pool = last_pool if last_pool else []

        # 1) 当日开盘可买入：选股后次日开盘买，不重复买入，且不超过 max_positions、用 cash 买入
        held_codes = {p["code"] for p in positions}
        slots = max_positions - len(positions)
        if pool and slots > 0 and cash > 0:
            candidates = []
            for r in pool:
                if r["code"] in held_codes:
                    continue
                open_t = _get_price(price_cache, r["code"], t, "open", get_kline_fn)
                if not open_t or open_t <= 0:
                    continue
                candidates.append((r, open_t))
                if len(candidates) >= slots:
                    break
            if candidates:
                n_new = len(candidates)  # 当前可买股票个数
                size_per = cash / n_new  # 每只仓位 = 当前现金 / 当前可买股票个数
                for (r, open_t) in candidates:
                    positions.append({
                        "code": r["code"],
                        "name": r.get("name", ""),
                        "buy_date": t,
                        "buy_price": open_t,
                        "position_size": size_per,
                    })
                    held_codes.add(r["code"])
                cash = 0.0

        # 2) 检查退出：仅对 buy_date < t 的持仓检查（T+1 买入日不卖），用当日 high/low 判断
        exited_today = []
        still_held = []
        for p in positions:
            if p["buy_date"] == t:
                still_held.append(p)
                continue
            code, buy_price = p["code"], p["buy_price"]
            high_t = _get_price(price_cache, code, t, "high", get_kline_fn)
            low_t = _get_price(price_cache, code, t, "low", get_kline_fn)
            if high_t is None or low_t is None:
                still_held.append(p)
                continue
            # 弱势调仓：持有满 hold_days 日且该期间内从未超过买入价+hold_strong_pct% 则调出
            if hold_days is not None and hold_strong_pct is not None:
                try:
                    idx_buy = dates.index(p["buy_date"])
                except ValueError:
                    idx_buy = -1
                if idx_buy >= 0:
                    held_days = i - idx_buy
                    if held_days == hold_days and idx_buy + hold_days < len(dates):
                        highs = []
                        for j in range(1, hold_days + 1):
                            d = dates[idx_buy + j]
                            h = _get_price(price_cache, code, d, "high", get_kline_fn)
                            highs.append(h)
                        if all(h is not None for h in highs):
                            threshold = buy_price * (1.0 + hold_strong_pct)
                            if max(highs) < threshold:
                                exit_price_w = _get_price(price_cache, code, t, "close", get_kline_fn) or buy_price
                                exited_today.append({
                                    **p,
                                    "sell_date": t,
                                    "exit_reason": "weak_exit",
                                    "exit_price": exit_price_w,
                                })
                                all_trades.append({
                                    "code": p["code"],
                                    "name": p["name"],
                                    "buy_date": p["buy_date"],
                                    "sell_date": t,
                                    "exit_reason": "weak_exit",
                                    "entry": p["buy_price"],
                                    "exit_price": exit_price_w,
                                })
                                continue
            open_next = _get_price(price_cache, code, dates[i + 1], "open", get_kline_fn) if i + 1 < len(dates) else None
            ma20_sell_price = None
            if break_ma20_pct is not None:
                ma20 = _get_ma20(price_cache, code, t, get_kline_fn)
                if ma20 is not None and ma20 > 0:
                    ma20_sell_price = ma20 * (1.0 - break_ma20_pct)
            has_exit_rules = stop_loss is not None or take_profit is not None or break_ma20_pct is not None
            if not has_exit_rules:
                # 未配置止盈止损时：持有 1 日后次日开盘卖
                if p["buy_date"] == dates[i - 1]:
                    exit_p = _get_price(price_cache, code, t, "open", get_kline_fn) or buy_price
                    exited_today.append({**p, "sell_date": t, "exit_reason": "normal", "exit_price": exit_p})
                    all_trades.append({"code": p["code"], "name": p["name"], "buy_date": p["buy_date"], "sell_date": t, "exit_reason": "normal", "entry": p["buy_price"], "exit_price": exit_p})
                else:
                    still_held.append(p)
                continue
            exit_p, exit_reason, sd = _exit_price_with_stop_target(
                buy_price, high_t, low_t, open_next or buy_price,
                stop_loss, take_profit, ma20_sell_price,
            )
            # 仅在实际触发止盈/止损/破线时卖出；normal 表示继续持有
            if exit_reason != "normal":
                sell_date = t  # 触发当日卖出（T+1 下可卖，因买入日 < t）
                exited_today.append({
                    **p,
                    "sell_date": sell_date,
                    "exit_reason": exit_reason,
                    "exit_price": exit_p,
                })
                all_trades.append({
                    "code": p["code"],
                    "name": p["name"],
                    "buy_date": p["buy_date"],
                    "sell_date": sell_date,
                    "exit_reason": exit_reason,
                    "entry": p["buy_price"],
                    "exit_price": exit_p,
                })
            else:
                still_held.append(p)
        positions = still_held

        # 退出变现：卖出所得加入现金
        for x in exited_today:
            cash += x["position_size"] * (x["exit_price"] / x["buy_price"])

        # 4) 当日组合市值（元）：现金 + 持仓市值（按 close 计价）
        value_held = 0.0
        for p in positions:
            close_t = _get_price(price_cache, p["code"], t, "close", get_kline_fn)
            if close_t and close_t > 0:
                value_held += p["position_size"] * (close_t / p["buy_price"])
        value_end = cash + value_held
        if value_prev <= 0:
            daily_ret = 0.0
        else:
            daily_ret = (value_end - value_prev) / value_prev
        daily_returns.append(daily_ret)
        value_prev = value_end if value_end > 0 else value_prev

        trades_today = [
            {"code": x["code"], "name": x["name"], "buy_date": x["buy_date"], "sell_date": x["sell_date"], "exit_reason": x["exit_reason"], "entry": x["buy_price"], "exit_price": x["exit_price"]}
            for x in exited_today
        ]
        pool_history.append({
            "date": t,
            "pool": [p["code"] for p in positions[:5]],
            "ret": daily_ret,
            "trades": trades_today,
        })

        if day_idx % log_every == 0 or day_idx == total_days - 1:
            print(f"  [{day_idx + 1}/{total_days}] {t} 持仓 {len(positions)} 只 退出 {len(exited_today)} 只 日收益 {daily_ret:+.2%}", flush=True)

    # 汇总
    series = pd.Series(daily_returns)
    cum = (1 + series).cumprod()
    total_ret = cum.iloc[-1] - 1.0 if len(cum) > 0 else 0.0
    n_years = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 365.25
    annual_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1.0 if n_years > 0 else 0.0
    max_dd = (cum / cum.cummax() - 1.0).min() if len(cum) > 0 else 0.0

    # 日收益对应日期
    trade_dates = [pool_history[k]["date"] for k in range(len(pool_history))]
    return {
        "total_return": total_ret,
        "annual_return": annual_ret,
        "max_drawdown": max_dd,
        "trading_days": len(daily_returns),
        "daily_returns": daily_returns,
        "cumulative": cum.tolist(),
        "pool_history": pool_history,
        "trade_dates": trade_dates,
        "initial_capital": initial_capital,
        "final_value": value_prev,
    }


def _write_detailed_report(
    config: dict, result: dict, report_path: Path, csv_dir: Path, report_timestamp: str | None = None
) -> None:
    """生成详细回测报告（含月度收益、日收益统计、样本池、曲线数据 CSV）。
    report_timestamp 非空时 CSV 与报告内文件名使用时间戳，避免覆盖历史结果。"""
    start_date = config.get("start_date", "")
    end_date = config.get("end_date", "")
    strategy_name = config.get("strategy", "trend_ma")
    universe = config.get("universe", "zz500")
    benchmark = config.get("benchmark", "sh.000852")

    total_ret = result["total_return"]
    annual_ret = result["annual_return"]
    max_dd = result["max_drawdown"]
    trading_days = result["trading_days"]
    daily_returns = result["daily_returns"]
    cum = result["cumulative"]
    pool_history = result["pool_history"]
    trade_dates = result.get("trade_dates", [])
    initial_capital = result.get("initial_capital")
    final_value = result.get("final_value")

    series = pd.Series(daily_returns)
    win_rate = (series > 0).sum() / len(series) * 100 if len(series) > 0 else 0
    daily_mean = series.mean() * 100 if len(series) > 0 else 0
    daily_std = series.std() * 100 if len(series) > 0 else 0
    daily_min = series.min() * 100 if len(series) > 0 else 0
    daily_max = series.max() * 100 if len(series) > 0 else 0

    # 最大回撤发生区间
    cum_s = pd.Series(cum)
    cummax = cum_s.cummax()
    drawdown = cum_s / cummax - 1.0
    if len(drawdown) > 0 and trade_dates:
        dd_min_idx = drawdown.idxmin()
        # 回撤结束日
        dd_end_date = trade_dates[dd_min_idx] if dd_min_idx < len(trade_dates) else ""
        # 回撤开始日：该回撤段内 cummax 首次达到峰值的日期
        peak_idx = cum_s.iloc[: dd_min_idx + 1].idxmax() if dd_min_idx >= 0 else 0
        dd_start_date = trade_dates[peak_idx] if peak_idx < len(trade_dates) else ""
        max_dd_period = f"{dd_start_date} ~ {dd_end_date}" if dd_start_date and dd_end_date else "-"
    else:
        max_dd_period = "-"

    # 月度收益
    monthly_rows = []
    if trade_dates and len(trade_dates) == len(daily_returns):
        df_day = pd.DataFrame({"date": trade_dates, "ret": daily_returns})
        df_day["date"] = pd.to_datetime(df_day["date"])
        df_day["ym"] = df_day["date"].dt.to_period("M")
        monthly = df_day.groupby("ym")["ret"].apply(lambda x: (1 + x).prod() - 1).reset_index()
        monthly["ym"] = monthly["ym"].astype(str)
        cum_month = (1 + monthly["ret"]).cumprod()
        monthly["cumulative"] = cum_month
        monthly_rows = monthly.to_dict("records")
    else:
        monthly_rows = []

    sl_pct = config.get("stop_loss_pct")
    tp_pct = config.get("take_profit_pct")
    bm_pct = config.get("break_ma20_pct")
    hold_days_cfg = config.get("hold_days")
    hold_strong_cfg = config.get("hold_strong_pct")
    stop_str = f"止损 {sl_pct}%" if sl_pct is not None else "不止损"
    target_str = f"止盈 {tp_pct}%" if tp_pct is not None else "不止盈"
    break_str = f"破MA20 {bm_pct}%卖出" if bm_pct is not None else "无"
    weak_str = f"弱势调仓 {hold_days_cfg}日未过+{hold_strong_cfg}%" if hold_days_cfg is not None and hold_strong_cfg is not None else "无"
    lines = [
        "# 回测详细报告",
        "",
        f"- **策略**: {strategy_name}",
        f"- **宇宙**: {universe}",
        f"- **基准**: {benchmark}",
        f"- **区间**: {start_date} ~ {end_date}",
        f"- **止盈止损**: {stop_str}, {target_str}, {break_str}, {weak_str}",
        f"- **初始资金**: {config.get('initial_capital', 500000):,.0f} 元，**最多持仓**: {config.get('max_positions', 5)} 只",
        "",
        "## 1. 收益与风险汇总",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
    ]
    if initial_capital is not None and final_value is not None:
        lines.append(f"| 初始资金 | {initial_capital:,.0f} 元 |")
        lines.append(f"| 期末资产 | {final_value:,.0f} 元 |")
    lines.extend([
        f"| 总收益率 | {total_ret:.2%} |",
        f"| 年化收益率 | {annual_ret:.2%} |",
        f"| 最大回撤 | {max_dd:.2%} |",
        f"| 最大回撤区间 | {max_dd_period} |",
        f"| 交易日数 | {trading_days} |",
        "",
        "## 2. 日收益统计",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 日收益均值 | {daily_mean:.3f}% |",
        f"| 日收益标准差 | {daily_std:.3f}% |",
        f"| 日收益最小值 | {daily_min:.2f}% |",
        f"| 日收益最大值 | {daily_max:.2f}% |",
        f"| 日胜率 | {win_rate:.1f}% |",
        "",
        "## 3. 月度收益",
        "",
    ])
    if monthly_rows:
        lines.append("| 年月 | 月收益率 | 累计净值 |")
        lines.append("|------|----------|----------|")
        for r in monthly_rows:
            lines.append(f"| {r['ym']} | {r['ret']:.2%} | {r['cumulative']:.4f} |")
    else:
        lines.append("（无日期对应，未计算月度）")
    lines.extend(["", "## 4. 每日交易明细（首尾各 5 个交易日，含每只股票买入日/卖出日/退出原因）", ""])

    for label, indices in [("首 5 日", range(min(5, len(pool_history)))), ("末 5 日", range(max(0, len(pool_history) - 5), len(pool_history)))]:
        lines.append(f"### {label}")
        lines.append("")
        for i in indices:
            if i >= len(pool_history):
                continue
            h = pool_history[i]
            lines.append(f"- **{h['date']}** 日收益 {h['ret']:.2%}")
            trades = h.get("trades") or []
            if trades:
                lines.append("  | 股票 | 买入日 | 卖出日 | 退出原因 |")
                lines.append("  |------|--------|--------|----------|")
                for t in trades:
                    lines.append(f"  | {t.get('code','')} {t.get('name','')} | {t.get('buy_date','')} | {t.get('sell_date','')} | {t.get('exit_reason','')} |")
            else:
                pool_str = ", ".join(h["pool"]) if h["pool"] else "（空）"
                lines.append(f"  池: {pool_str}")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 日频曲线 CSV（带时间戳时防覆盖）
    daily_suffix = f"_{report_timestamp}" if report_timestamp else ""
    csv_daily_name = f"backtest_daily{daily_suffix}.csv"
    csv_path = csv_dir / csv_daily_name
    if trade_dates and len(trade_dates) == len(daily_returns) and len(trade_dates) == len(cum):
        df_curve = pd.DataFrame({
            "date": trade_dates,
            "daily_return": daily_returns,
            "cumulative": cum,
        })
        df_curve.to_csv(csv_path, index=False, encoding="utf-8-sig")
    # 全部交易明细 CSV（每只股票买入日、卖出日、退出原因）
    all_trades = []
    for h in pool_history:
        for t in h.get("trades") or []:
            row = {
                "hold_date": h["date"],
                "code": t.get("code", ""),
                "name": t.get("name", ""),
                "buy_date": t.get("buy_date", ""),
                "sell_date": t.get("sell_date", ""),
                "exit_reason": t.get("exit_reason", ""),
                "entry": t.get("entry", ""),
                "exit_price": t.get("exit_price", ""),
            }
            if row["entry"] and row["exit_price"]:
                row["ret_pct"] = (float(row["exit_price"]) / float(row["entry"]) - 1.0) * 100
            else:
                row["ret_pct"] = ""
            all_trades.append(row)
    trades_suffix = f"_{report_timestamp}" if report_timestamp else ""
    csv_trades_name = f"backtest_trades{trades_suffix}.csv"
    if all_trades:
        pd.DataFrame(all_trades).to_csv(csv_dir / csv_trades_name, index=False, encoding="utf-8-sig")
    with open(report_path, "a", encoding="utf-8") as f:
        f.write("## 5. 数据文件\n\n")
        f.write(f"- 日频曲线: `output/{csv_daily_name}`（date, daily_return, cumulative）\n")
        if all_trades:
            f.write(f"- 全部交易明细: `output/{csv_trades_name}`（hold_date, code, name, buy_date, sell_date, exit_reason, entry, exit_price, ret_pct）\n")


def main():
    parser = argparse.ArgumentParser(description="选股策略回测")
    parser.add_argument("--config", "-c", default="bt_config.yaml", help="配置文件路径")
    parser.add_argument("--strategy", default=None, help="覆盖配置中的策略名")
    parser.add_argument("--start", default=None, help="回测起始日期")
    parser.add_argument("--end", default=None, help="回测结束日期（最后一天）")
    parser.add_argument("--pool-screen-start", default=None, help="选股开始日，不填则用 start")
    parser.add_argument("--pool-screen-end", default=None, help="选股结束日，不填则用 end；设短则此后沿用最后池子")
    args = parser.parse_args()

    config = _load_config(args.config)
    if args.strategy:
        config["strategy"] = args.strategy
    if args.start:
        config["start_date"] = args.start
    if args.end:
        config["end_date"] = args.end
    if args.pool_screen_start:
        config["pool_screen_start"] = args.pool_screen_start
    if args.pool_screen_end:
        config["pool_screen_end"] = args.pool_screen_end

    config.setdefault("start_date", "2023-01-01")
    if config.get("end_date") is None or config.get("end_date") == "":
        config["end_date"] = dt.date.today().isoformat()

    print(f"回测: {config['start_date']} ~ {config['end_date']}", flush=True)
    print(f"  策略={config.get('strategy', 'trend_ma')}, 宇宙={config.get('universe', 'zz500')}", flush=True)

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("BaoStock 登录失败：" + lg.error_msg)

    try:
        result = run_backtest(config)
        if "error" in result:
            print(f"错误: {result['error']}", flush=True)
            return

        print(f"\n--- 回测结果 ---", flush=True)
        if result.get("initial_capital") is not None and result.get("final_value") is not None:
            print(f"  初始资金: {result['initial_capital']:,.0f} 元 → 期末资产: {result['final_value']:,.0f} 元", flush=True)
        print(f"  总收益: {result['total_return']:.2%}", flush=True)
        print(f"  年化收益: {result['annual_return']:.2%}", flush=True)
        print(f"  最大回撤: {result['max_drawdown']:.2%}", flush=True)
        print(f"  交易日数: {result['trading_days']}", flush=True)

        out_dir = Path("output")
        out_dir.mkdir(exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = out_dir / f"backtest_report_{ts}.md"
        _write_detailed_report(config, result, report_path, out_dir, report_timestamp=ts)
        print(f"\n报告已保存: {report_path}", flush=True)
        csv_daily = out_dir / f"backtest_daily_{ts}.csv"
        csv_trades = out_dir / f"backtest_trades_{ts}.csv"
        if csv_daily.exists():
            print(f"日频曲线已保存: {csv_daily}", flush=True)
        if csv_trades.exists():
            print(f"交易明细已保存: {csv_trades}", flush=True)

    finally:
        bs.logout()


if __name__ == "__main__":
    main()

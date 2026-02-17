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
    strategy_name = config.get("strategy", "trend_ma")
    strategy_params = config.get("strategy_params", {})
    universe = config.get("universe", "zz500")
    benchmark = config.get("benchmark", "sh.000852")
    start_date = config.get("start_date", "2023-01-01")
    end_date = config.get("end_date", dt.date.today().isoformat())
    pool_size = config.get("pool_size", 20)
    adjustflag = config.get("adjustflag", "2")
    allow_bj = config.get("allow_bj", False)

    strategy_params["pool_size"] = strategy_params.get("pool_size", pool_size)
    strategy_cls = get_strategy(strategy_name)
    strategy = strategy_cls(config=strategy_params)

    def get_kline_fn(code: str, end_date: str):
        return load_kline(code, end_date, lookback_days=500, adjustflag=adjustflag)

    dates = get_trading_dates(start_date, end_date)
    if len(dates) < 3:
        return {"error": "交易日不足", "dates": dates}

    price_cache = {}
    daily_returns = []
    pool_history = []

    for i in range(1, len(dates) - 1):
        t_prev = dates[i - 1]  # 筛选日
        t_curr = dates[i]      # 当日 open 持有
        t_next = dates[i + 1]  # 次日 open 调仓

        bench_ret = _compute_benchmark_ret20(benchmark, t_prev, adjustflag)
        if bench_ret is None:
            daily_returns.append(0.0)
            continue

        uni_df = get_stock_list(universe, query_date=t_prev, allow_bj=allow_bj, as_of_date=t_prev)
        pool = strategy.screen(t_prev, uni_df, get_kline_fn, bench_ret)

        if not pool:
            daily_returns.append(0.0)
            pool_history.append({"date": t_curr, "pool": [], "ret": 0.0})
            continue

        # 等权：每只 1/N
        n = len(pool)
        value_curr = 0.0
        value_next = 0.0
        valid = 0

        for r in pool:
            code = r["code"]
            open_curr = _get_price(price_cache, code, t_curr, "open", get_kline_fn)
            open_next = _get_price(price_cache, code, t_next, "open", get_kline_fn)
            if open_curr and open_curr > 0 and open_next and open_next > 0:
                value_curr += open_curr / n
                value_next += open_next / n
                valid += 1

        if valid == 0 or value_curr <= 0:
            daily_returns.append(0.0)
        else:
            ret = value_next / value_curr - 1.0
            daily_returns.append(ret)

        pool_history.append({
            "date": t_curr,
            "pool": [x["code"] for x in pool[:5]],  # 仅记前5只
            "ret": daily_returns[-1],
        })

    # 汇总
    series = pd.Series(daily_returns)
    cum = (1 + series).cumprod()
    total_ret = cum.iloc[-1] - 1.0 if len(cum) > 0 else 0.0
    n_years = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 365.25
    annual_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1.0 if n_years > 0 else 0.0
    max_dd = (cum / cum.cummax() - 1.0).min() if len(cum) > 0 else 0.0

    return {
        "total_return": total_ret,
        "annual_return": annual_ret,
        "max_drawdown": max_dd,
        "trading_days": len(daily_returns),
        "daily_returns": daily_returns,
        "cumulative": cum.tolist(),
        "pool_history": pool_history,
    }


def main():
    parser = argparse.ArgumentParser(description="选股策略回测")
    parser.add_argument("--config", "-c", default="bt_config.yaml", help="配置文件路径")
    parser.add_argument("--strategy", default=None, help="覆盖配置中的策略名")
    parser.add_argument("--start", default=None, help="覆盖起始日期")
    parser.add_argument("--end", default=None, help="覆盖结束日期")
    args = parser.parse_args()

    config = _load_config(args.config)
    if args.strategy:
        config["strategy"] = args.strategy
    if args.start:
        config["start_date"] = args.start
    if args.end:
        config["end_date"] = args.end

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
        print(f"  总收益: {result['total_return']:.2%}", flush=True)
        print(f"  年化收益: {result['annual_return']:.2%}", flush=True)
        print(f"  最大回撤: {result['max_drawdown']:.2%}", flush=True)
        print(f"  交易日数: {result['trading_days']}", flush=True)

        out_dir = Path("output")
        out_dir.mkdir(exist_ok=True)
        report_path = out_dir / "backtest_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# 回测报告\n\n")
            f.write(f"- 策略: {config.get('strategy')}\n")
            f.write(f"- 区间: {config['start_date']} ~ {config['end_date']}\n")
            f.write(f"- 总收益: {result['total_return']:.2%}\n")
            f.write(f"- 年化收益: {result['annual_return']:.2%}\n")
            f.write(f"- 最大回撤: {result['max_drawdown']:.2%}\n")
        print(f"\n报告已保存: {report_path}", flush=True)

    finally:
        bs.logout()


if __name__ == "__main__":
    main()

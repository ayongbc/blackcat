#!/usr/bin/env python3
"""
日频选股日报生成器
调用 strategies 与 data 层，从 bt_config.yaml 读取配置，与回测共用同一套筛选逻辑与参数。
"""

import argparse
import os
from pathlib import Path

import pandas as pd
import baostock as bs

from data.kline_loader import load_kline, compute_benchmark_ret20
from data.universe import get_stock_list, last_trading_date
from strategies import get_strategy

OUT_DIR = "output"


def _load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        raise RuntimeError("YAML 配置需要 PyYAML: pip install pyyaml")
    except Exception as e:
        raise RuntimeError(f"读取配置失败: {e}") from e


def build_strategy_config(cfg: dict) -> dict:
    """合并 strategy_params 与顶层配置，供策略使用"""
    params = cfg.get("strategy_params") or {}
    return {
        **params,
        "benchmark": cfg.get("benchmark", "sh.000852"),
        "adjustflag": str(cfg.get("adjustflag", "2")),
        "allow_bj": cfg.get("allow_bj", False),
        "universe": cfg.get("universe", "zz500"),
    }


def run_screen(config: dict, end_date: str, max_symbols: int | None) -> list[dict]:
    """执行选股，返回候选池 list[dict]"""
    strategy_name = config.get("strategy", "trend_ma")
    StrategyClass = get_strategy(strategy_name)
    strategy_config = build_strategy_config(config)
    strategy = StrategyClass(config=strategy_config)

    universe_df = get_stock_list(
        universe=config.get("universe", "zz500"),
        query_date=end_date,
        allow_bj=config.get("allow_bj", False),
    )
    if isinstance(max_symbols, int) and max_symbols > 0:
        universe_df = universe_df.head(max_symbols)

    bench_ret20 = compute_benchmark_ret20(
        config.get("benchmark", "sh.000852"),
        end_date,
        config.get("adjustflag", "2"),
    )
    if bench_ret20 is None:
        raise RuntimeError("基准指数数据不足，检查 benchmark 代码/数据是否成功拉取")

    adjustflag = config.get("adjustflag", "2")
    lookback_days = strategy_config.get("lookback_days", 500)
    min_bars = strategy_config.get("min_bars", 160)
    lookback_days = max(lookback_days, min_bars + 100)

    def get_kline_fn(code: str, date: str):
        return load_kline(code, date, lookback_days=lookback_days, adjustflag=adjustflag)

    print(
        f"Universe={config.get('universe')} size={len(universe_df)} | "
        f"date={end_date} | benchmark_ret20={bench_ret20:.4f}",
        flush=True,
    )

    rows = strategy.screen(
        date=end_date,
        universe_df=universe_df,
        get_kline_fn=get_kline_fn,
        benchmark_ret20=bench_ret20,
    )
    return rows


def write_reports(end_date: str, rows: list[dict], config: dict) -> None:
    """将选股结果写入 CSV 与 Markdown 报表"""
    universe = config.get("universe", "zz500")
    pool_size = config.get("strategy_params") or {}
    pool_size = pool_size.get("pool_size", 50)
    csv_path = os.path.join(OUT_DIR, f"{end_date}_{universe}_pool.csv")
    md_path = os.path.join(OUT_DIR, f"{end_date}_{universe}_report.md")

    os.makedirs(OUT_DIR, exist_ok=True)

    if not rows:
        pool = pd.DataFrame()
    else:
        df = pd.DataFrame(rows)
        df = df.sort_values("score", ascending=False).drop_duplicates(
            subset=["code"], keep="first"
        )
        pool = df.head(pool_size).reset_index(drop=True)

    pool.to_csv(csv_path, index=False)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Daily Pool {end_date}\n\n")
        f.write(f"- benchmark: {config.get('benchmark', 'sh.000852')}\n")
        f.write(f"- universe: {universe}\n")
        f.write(f"- pool_size: {pool_size}\n")
        sp = config.get("strategy_params") or {}
        f.write(f"- min_avg_amount_20: {sp.get('min_avg_amount_20', '-')}\n")
        f.write(f"- min_days_above_ma120: {sp.get('min_days_above_ma120', '-')}\n")
        f.write(f"- min_score: {sp.get('min_score', '-')}\n\n")
        if pool.empty:
            f.write("No candidates.\n")
        else:
            cols = ["code", "name", "setup", "score", "rs20", "vol_ratio", "atrp", "avg_amt20", "close"]
            available = [c for c in cols if c in pool.columns]
            f.write(pool[available].to_markdown(index=False))
            f.write("\n")

    print("Saved:", csv_path, flush=True)
    print("Saved:", md_path, flush=True)


def parse_args():
    p = argparse.ArgumentParser(description="日频选股日报，从 bt_config.yaml 读取配置")
    p.add_argument("--config", "-c", default="bt_config.yaml", help="配置文件路径")
    p.add_argument("--date", default=None, help="选股日期 YYYY-MM-DD，默认最近交易日")
    p.add_argument("--universe", default=None, choices=["all", "hs300", "zz500", "sz50", "zz1000", "zz2000"])
    p.add_argument("--pool-size", type=int, default=None, help="覆盖 config 中的 pool_size")
    p.add_argument("--max-symbols", type=int, default=None, help="调试用：限制宇宙大小")
    return p.parse_args()


def main():
    args = parse_args()
    root = Path(__file__).resolve().parent
    config_path = root / args.config if not Path(args.config).is_absolute() else Path(args.config)
    config = _load_config(str(config_path))

    if args.universe is not None:
        config["universe"] = args.universe
    if args.pool_size is not None:
        if "strategy_params" not in config:
            config["strategy_params"] = {}
        config["strategy_params"]["pool_size"] = args.pool_size

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("BaoStock 登录失败：" + lg.error_msg)

    end_date = args.date or last_trading_date()

    try:
        rows = run_screen(config, end_date, args.max_symbols)
        write_reports(end_date, rows, config)
    finally:
        bs.logout()


if __name__ == "__main__":
    main()

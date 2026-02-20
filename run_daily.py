#!/usr/bin/env python3
"""
日频选股日报生成器
调用 strategies 与 data 层，从 bt_config.yaml 读取配置，与回测共用同一套筛选逻辑与参数。
"""

import argparse
import math
import os
import time
from pathlib import Path

import pandas as pd
import baostock as bs

from data.kline_loader import load_kline, compute_benchmark_ret20
from data.universe import get_stock_list, last_trading_date
from strategies import get_strategy


def _load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            raise RuntimeError("YAML 配置需要 PyYAML: pip install pyyaml")
    return {}


def build_strategy_config(cfg: dict) -> dict:
    """合并 strategy_params 与顶层配置，供策略使用"""
    params = cfg.get("strategy_params") or {}
    benchmark_cfg = cfg.get("benchmark", "sh.000852")
    universe = cfg.get("universe", "zz500")
    if isinstance(benchmark_cfg, dict):
        benchmark = benchmark_cfg.get(universe) or benchmark_cfg.get("default") or "sh.000852"
    else:
        benchmark = str(benchmark_cfg) if benchmark_cfg else "sh.000852"
    return {
        **params,
        "benchmark": benchmark,
        "adjustflag": str(cfg.get("adjustflag", "2")),
        "allow_bj": cfg.get("allow_bj", False),
        "universe": universe,
    }

OUT_DIR = "output"
DATA_DIR = "data"

CONFIG = {
    # 基准指数：中证1000
    "benchmark": "sh.000852",

    # 选股宇宙：all | hs300 | zz500 | sz50 | zz1000 | zz2000（zz1000/zz2000 需 akshare）
    "universe": "zz500",

    # 复权：BaoStock adjustflag: 1=后复权, 2=前复权, 3=不复权
    "adjustflag": "2",

    # 流动性过滤：近20日平均成交额（amount）阈值，单位：元（5000万）
    "min_avg_amount_20": 5e7,

    # 数据条数过滤：至少覆盖 120MA + 缓冲
    "min_bars": 160,

    # 回踩触及 MA20 容忍度（例如 2%）
    "pullback_touch_tol": 0.02,

    # 放量倍数（突破策略）
    "breakout_vol_ratio": 1.5,

    # 防追高：收盘相对 MA20 不超过 +8%
    "max_close_over_ma20": 1.08,

    # 趋势筛选增强
    "min_rs20": 0.0,              # 正向相对强度（相对基准）
    "min_days_above_ma120": 30,   # 过去 N 天收盘价均在 MA120 之上
    "min_trend_days": 10,         # MA20 > MA60 至少维持天数
    "pullback_max_vol_ratio": 1.2,  # 回踩时量比上限（缩量确认）
    "min_score": 25.0,            # 最低评分门槛

    # 输出股票池规模
    "pool_size": 50,

    # 是否包含北交所
    "allow_bj": False,

    # 性能：每只股票取近多少天数据（越小越快；需 >= 120MA + 缓冲）
    "lookback_days": 420,

    # 性能：抓取单只股票K线后的短暂停顿（秒），降低被服务端限流/卡住概率
    "sleep_s": 0.02,
}


def ma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(n).mean()
    return atr / close


def ensure_dirs():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "kline"), exist_ok=True)


def fetch_kline(code: str, start_date: str, end_date: str, adjustflag: str) -> pd.DataFrame:
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
    while (rs.error_code == "0") and rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=fields.split(","))
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_or_update_kline(code: str, end_date: str, adjustflag: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "kline", code.replace(".", "_") + ".csv")

    if os.path.exists(path):
        old = pd.read_csv(path, parse_dates=["date"])
        last = old["date"].max()
        # 本地已包含 end_date，直接复用，不发 API 请求
        if pd.to_datetime(last) >= pd.to_datetime(end_date):
            return old

        start = (last + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        new = fetch_kline(code, start, end_date, adjustflag)
        df = (
            pd.concat([old, new], ignore_index=True)
            .drop_duplicates(subset=["date"])
            .sort_values("date")
        )
    else:
        lookback = int(CONFIG.get("lookback_days", 420))
        start = (pd.to_datetime(end_date) - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")
        df = fetch_kline(code, start, end_date, adjustflag)

    if len(df):
        df.to_csv(path, index=False)

    # 仅在实际发起 API 请求后 sleep，避免限流
    sleep_s = float(CONFIG.get("sleep_s", 0.0))
    if sleep_s > 0:
        time.sleep(sleep_s)

    return df


def _akshare_code_to_baostock(s: str) -> str:
    """AKShare/CSI 格式 (600549.SH) -> BaoStock 格式 (sh.600549)"""
    s = str(s).strip().upper()
    if ".SH" in s:
        return "sh." + s.split(".SH")[0]
    if ".SZ" in s:
        return "sz." + s.split(".SZ")[0]
    if ".BJ" in s:
        return "bj." + s.split(".BJ")[0]
    # 纯数字：6开头上交所，0/3 开头深交所
    if s.startswith("6"):
        return "sh." + s
    return "sz." + s


def score_one(df: pd.DataFrame, bench_ret20: float):
    # indicators
    df["ma5"] = ma(df["close"], 5)
    df["ma20"] = ma(df["close"], 20)
    df["ma60"] = ma(df["close"], 60)
    df["ma120"] = ma(df["close"], 120)
    df["vol_ma20"] = ma(df["volume"], 20)
    df["avg_amt20"] = ma(df["amount"], 20)
    df["atrp"] = atr_pct(df, 14)
    df["ret20"] = df["close"].pct_change(20)

    d = df.dropna().iloc[-1]

    # liquidity
    if float(d["avg_amt20"]) < CONFIG["min_avg_amount_20"]:
        return None

    # trend precondition: MA5 > MA20 > MA60 > MA120 and MA20 rising
    ma20_up = df["ma20"].iloc[-1] > df["ma20"].iloc[-6]
    if not (d["ma5"] > d["ma20"] > d["ma60"] > d["ma120"] and ma20_up):
        return None

    # 股价在 MA120 之上 N 天
    n_above = CONFIG["min_days_above_ma120"]
    recent_above = df.iloc[-n_above:]
    if not (recent_above["close"] > recent_above["ma120"]).all():
        return None

    # MA20 > MA60 至少维持 N 天
    n_trend = CONFIG["min_trend_days"]
    recent_trend = df.iloc[-n_trend:]
    if not (recent_trend["ma20"] > recent_trend["ma60"]).all():
        return None

    close = float(d["close"])
    ma20v = float(d["ma20"])
    if close / ma20v > CONFIG["max_close_over_ma20"]:
        return None

    # breakout setup
    prev20_max_close = df["close"].shift(1).rolling(20).max().iloc[-1]
    vol_ratio = float(d["volume"]) / float(d["vol_ma20"]) if float(d["vol_ma20"]) > 0 else 0
    is_breakout = (close >= float(prev20_max_close)) and (vol_ratio >= CONFIG["breakout_vol_ratio"])

    # pullback setup（回踩时要求缩量确认）
    tol = CONFIG["pullback_touch_tol"]
    recent = df.iloc[-6:-1]  # prev 5 days
    touched = (
        (recent["low"] <= recent["ma20"] * (1 + tol)) & (recent["low"] >= recent["ma20"] * (1 - tol))
    ).any()
    is_pullback = touched and (close > ma20v) and (vol_ratio <= CONFIG["pullback_max_vol_ratio"])

    if not (is_breakout or is_pullback):
        return None

    # scoring
    rs20 = float(d["ret20"]) - float(bench_ret20)
    if rs20 < CONFIG["min_rs20"]:
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

    setup = ("breakout" if is_breakout else "") + ("+pullback" if (is_breakout and is_pullback) else ("pullback" if is_pullback else ""))
    code = str(df["code"].iloc[-1]) if "code" in df.columns else ""
    return {
        "code": code,
        "name": "",
        "setup": setup,
        "score": score,
        "rs20": rs20,
        "vol_ratio": vol_ratio,
        "atrp": risk,
        "avg_amt20": float(d["avg_amt20"]),
        "close": close,
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

    benchmark_cfg = config.get("benchmark", "sh.000905")
    universe = config.get("universe", "zz500")
    if isinstance(benchmark_cfg, dict):
        benchmark = benchmark_cfg.get(universe) or benchmark_cfg.get("default") or "sh.000905"
    else:
        benchmark = str(benchmark_cfg) if benchmark_cfg else "sh.000905"
    adjustflag = config.get("adjustflag", "2")
    bench_ret20 = compute_benchmark_ret20(benchmark, end_date, adjustflag)
    if bench_ret20 is None:
        raise RuntimeError("基准指数数据不足，检查 benchmark 代码/数据是否成功拉取")

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
    """将选股结果写入 CSV 与 Markdown 报表，文件名含策略 id 避免不同策略覆盖"""
    universe = config.get("universe", "zz500")
    strategy = config.get("strategy", "trend_ma")
    strategy_params = config.get("strategy_params") or {}
    pool_size = strategy_params.get("pool_size", 50)
    csv_path = os.path.join(OUT_DIR, f"{end_date}_{universe}_{strategy}_pool.csv")
    md_path = os.path.join(OUT_DIR, f"{end_date}_{universe}_{strategy}_report.md")

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

    bc = config.get("benchmark")
    benchmark_str = (
        (bc.get(universe) or bc.get("default") or "sh.000905") if isinstance(bc, dict)
        else (str(bc) if bc else "sh.000905")
    )
    min_amt = strategy_params.get("min_avg_amount_20", 5e7)
    min_days_ma120 = strategy_params.get("min_days_above_ma120", "-")
    min_score = strategy_params.get("min_score", "-")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Daily Pool {end_date}\n\n")
        f.write(f"- strategy: {strategy}\n")
        f.write(f"- benchmark: {benchmark_str}\n")
        f.write(f"- universe: {universe}\n")
        f.write(f"- pool_size: {pool_size}\n")
        f.write(f"- min_avg_amount_20: {min_amt:.0f}\n")
        f.write(f"- min_days_above_ma120: {min_days_ma120}\n")
        f.write(f"- min_score: {min_score}\n\n")
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

    try:
        end_date = args.date or last_trading_date()
        rows = run_screen(config, end_date, args.max_symbols)
        write_reports(end_date, rows, config)
    finally:
        bs.logout()


if __name__ == "__main__":
    main()

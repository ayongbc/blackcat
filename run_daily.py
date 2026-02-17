# run_daily.py
# A-share daily pool screener (MA + price/volume) using BaoStock + AKShare

import os
import math
import time
import json
import argparse
import datetime as dt
from typing import Optional

import pandas as pd
import baostock as bs

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

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


def last_trading_date() -> str:
    today = dt.date.today().strftime("%Y-%m-%d")
    rs = bs.query_trade_dates(
        start_date=(dt.date.today() - dt.timedelta(days=30)).strftime("%Y-%m-%d"),
        end_date=today,
    )
    tds = []
    while (rs.error_code == "0") and rs.next():
        tds.append(rs.get_row_data())
    df_td = pd.DataFrame(tds, columns=rs.fields)
    df_td = df_td[df_td["is_trading_day"] == "1"]
    if df_td.empty:
        return today
    return str(df_td.iloc[-1]["calendar_date"])


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


def get_universe_codes() -> set[str]:
    u = CONFIG.get("universe", "all")
    if u == "all":
        return set()  # empty => no filter

    # zz1000 / zz2000 用 AKShare（BaoStock 无成分股接口）
    if u in ("zz1000", "zz2000"):
        if not HAS_AKSHARE:
            raise RuntimeError(f"universe={u} 需安装 akshare: pip install akshare")
        symbol = "000852" if u == "zz1000" else "932000"
        try:
            df = ak.index_stock_cons_weight_csindex(symbol=symbol)
        except Exception as e:
            raise RuntimeError(f"AKShare 拉取 {u} 成分股失败: {e}") from e
        # 成分券代码列名可能是 成分券代码 / con_code / code
        col = None
        for c in ("成分券代码", "con_code", "code"):
            if c in df.columns:
                col = c
                break
        if col is None:
            raise RuntimeError(f"AKShare 返回列中未找到成分股代码，列: {list(df.columns)}")
        codes = {_akshare_code_to_baostock(x) for x in df[col].dropna().astype(str)}
        return codes

    query_date = last_trading_date()
    if u == "hs300":
        rs = bs.query_hs300_stocks(date=query_date)
    elif u == "zz500":
        rs = bs.query_zz500_stocks(date=query_date)
    elif u == "sz50":
        rs = bs.query_sz50_stocks(date=query_date)
    else:
        raise ValueError(f"Unknown universe: {u}")

    codes = set()
    while (rs.error_code == "0") and rs.next():
        codes.add(rs.get_row_data()[1])  # fields usually: date,code
    return codes


def get_stock_list() -> pd.DataFrame:
    allowed = get_universe_codes()

    rs = bs.query_stock_basic()
    data = []
    while (rs.error_code == "0") and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=rs.fields)

    if allowed:
        df = df[df["code"].isin(allowed)].copy()

    # 过滤退市/风险警示（简单版：名称含 ST / 退）
    df = df[df["status"] == "1"].copy()
    df = df[~df["code_name"].str.contains("ST", na=False)]
    df = df[~df["code_name"].str.contains("退", na=False)]

    if not CONFIG["allow_bj"]:
        df = df[~df["code"].str.startswith("bj.")]

    return df[["code", "code_name", "ipoDate"]].reset_index(drop=True)


def compute_benchmark_ret20(end_date: str):
    b = CONFIG["benchmark"]
    df = load_or_update_kline(b, end_date, CONFIG["adjustflag"])
    if df.empty or len(df) < 40:
        return None, None
    df["ret20"] = df["close"].pct_change(20)
    last = df.dropna().iloc[-1]
    return float(last["ret20"]), df


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

    return {
        "close": close,
        "vol_ratio": round(vol_ratio, 2),
        "avg_amt20": float(d["avg_amt20"]),
        "rs20": rs20,
        "trend_open": trend_open,
        "atrp": risk,
        "score": score,
        "setup": setup,
    }


def load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(path: str, state: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_batch(end_date: str, max_symbols: Optional[int], batch_size: int, resume: bool) -> pd.DataFrame:
    bench_ret20, _ = compute_benchmark_ret20(end_date)
    if bench_ret20 is None:
        raise RuntimeError("基准指数数据不足，检查 benchmark 代码/数据是否成功拉取")

    uni = get_stock_list()
    if isinstance(max_symbols, int) and max_symbols > 0:
        uni = uni.head(max_symbols)

    total = len(uni)

    state_path = os.path.join(DATA_DIR, "state.json")
    partial_path = os.path.join(DATA_DIR, f"partial_{end_date}.csv")

    if not resume:
        next_idx = 0
        if os.path.exists(partial_path):
            os.remove(partial_path)
    else:
        st = load_state(state_path)
        if st.get("date") != end_date:
            next_idx = 0
            if os.path.exists(partial_path):
                os.remove(partial_path)
        else:
            next_idx = int(st.get("next_idx", 0))

    if next_idx >= total:
        next_idx = 0
        # 避免再次续跑时往旧 partial 上追加导致同一 code 重复
        if os.path.exists(partial_path):
            os.remove(partial_path)

    end_idx = min(total, next_idx + batch_size)
    chunk = uni.iloc[next_idx:end_idx]

    print(f"Universe={CONFIG['universe']} size={total} | batch {next_idx}->{end_idx} | benchmark_ret20={bench_ret20:.4f}", flush=True)

    rows = []
    t0 = time.time()

    for j, r in enumerate(chunk.itertuples(index=False), start=1):
        code, name = r.code, r.code_name
        if j == 1 or j % 10 == 0:
            print(f"  [{j}/{len(chunk)}] {code} ... elapsed={time.time() - t0:.1f}s", flush=True)

        df = load_or_update_kline(code, end_date, CONFIG["adjustflag"])
        if df.empty or len(df) < CONFIG["min_bars"]:
            continue

        scored = score_one(df, bench_ret20)
        if not scored:
            continue

        rows.append(
            {
                "date": end_date,
                "code": code,
                "name": name,
                **scored,
            }
        )

    out = pd.DataFrame(rows)

    # append partial
    if len(out):
        header = not os.path.exists(partial_path)
        out.to_csv(partial_path, mode="a", index=False, header=header)

    # update state
    save_state(state_path, {"date": end_date, "next_idx": end_idx, "total": total, "universe": CONFIG["universe"]})

    # if finished all, build final pool from partial
    if end_idx >= total:
        if os.path.exists(partial_path):
            all_df = pd.read_csv(partial_path)
            if "code" in all_df.columns:
                all_df["code"] = all_df["code"].astype(str).str.strip()
            all_df = (
                all_df.sort_values("score", ascending=False)
                .drop_duplicates(subset=["code"], keep="first")
                .reset_index(drop=True)
            )
            min_score = CONFIG.get("min_score")
            if min_score is not None and min_score > 0:
                all_df = all_df[all_df["score"] >= min_score].reset_index(drop=True)
        else:
            all_df = pd.DataFrame()
        # reset for next run
        save_state(state_path, {"date": end_date, "next_idx": total, "total": total, "universe": CONFIG["universe"], "done": True})
        return all_df

    return pd.DataFrame()  # not finished yet


def write_reports(end_date: str, df: pd.DataFrame):
    universe = CONFIG["universe"]
    csv_path = os.path.join(OUT_DIR, f"{end_date}_{universe}_pool.csv")
    md_path = os.path.join(OUT_DIR, f"{end_date}_{universe}_report.md")

    if df is None or df.empty:
        pool = pd.DataFrame()
    else:
        # 去重：同一股票可能在断点续跑/重复批次里出现多次，保留最高分那条
        df2 = df.sort_values("score", ascending=False).drop_duplicates(subset=["code"], keep="first")
        pool = df2.head(CONFIG["pool_size"]).reset_index(drop=True)

    pool.to_csv(csv_path, index=False)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Daily Pool {end_date}\n\n")
        f.write(f"- benchmark: {CONFIG['benchmark']}\n")
        f.write(f"- universe: {CONFIG['universe']}\n")
        f.write(f"- pool_size: {CONFIG['pool_size']}\n")
        f.write(f"- min_avg_amount_20: {CONFIG['min_avg_amount_20']:.0f}\n")
        f.write(f"- min_days_above_ma120: {CONFIG.get('min_days_above_ma120', '-')}\n")
        f.write(f"- min_score: {CONFIG.get('min_score', '-')}\n\n")
        if pool.empty:
            f.write("No candidates yet (or still running batches).\n")
        else:
            f.write(pool[["code", "name", "setup", "score", "rs20", "vol_ratio", "atrp", "avg_amt20", "close"]].to_markdown(index=False))
            f.write("\n")

    print("Saved:", csv_path, flush=True)
    print("Saved:", md_path, flush=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default=None, choices=["all", "hs300", "zz500", "sz50", "zz1000", "zz2000"])
    p.add_argument("--pool-size", type=int, default=None)
    p.add_argument("--max-symbols", type=int, default=None, help="limit universe size for debug")
    p.add_argument("--batch-size", type=int, default=80, help="process how many symbols per run")
    p.add_argument("--no-resume", action="store_true", help="do not resume; start from scratch")
    p.add_argument("--auto", action="store_true", help="auto-run batches until complete")
    p.add_argument("--auto-sleep", type=float, default=0.5, help="sleep seconds between auto batches")
    p.add_argument("--max-minutes", type=float, default=0, help="stop auto mode after N minutes (0=unlimited)")
    return p.parse_args()


def main():
    ensure_dirs()
    args = parse_args()

    if args.universe:
        CONFIG["universe"] = args.universe
    if args.pool_size:
        CONFIG["pool_size"] = args.pool_size

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("BaoStock 登录失败：" + lg.error_msg)

    end_date = dt.date.today().isoformat()

    start_ts = time.time()

    def time_exceeded() -> bool:
        if not args.max_minutes or args.max_minutes <= 0:
            return False
        return (time.time() - start_ts) >= (args.max_minutes * 60)

    last_df_all = None

    try:
        while True:
            df_all = run_batch(
                end_date=end_date,
                max_symbols=args.max_symbols,
                batch_size=args.batch_size,
                resume=not args.no_resume,
            )
            last_df_all = df_all

            # 完成整轮
            if df_all is not None and not df_all.empty:
                write_reports(end_date, df_all)
                break

            # 未完成：写进度报表
            write_reports(end_date, pd.DataFrame())

            if not args.auto:
                print("Batch finished but not complete. Re-run the script to continue...", flush=True)
                break

            if time_exceeded():
                print("Auto mode stopped: max-minutes reached.", flush=True)
                break

            time.sleep(float(args.auto_sleep))

    finally:
        bs.logout()

    # if auto stopped without completion, keep latest progress report already written


if __name__ == "__main__":
    main()

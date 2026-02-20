#!/usr/bin/env python3
"""
补充 K 线数据：将指定宇宙成分股 + 基准的 K 线从 start_date 补充到 end_date
用于回测前准备数据
"""

import argparse
import datetime as dt
import logging

import baostock as bs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from data.kline_loader import supplement_kline
from data.universe import get_universe_codes, get_stock_list

# 各宇宙对应基准指数（与 bt_config 一致）
BENCHMARK_BY_UNIVERSE = {
    "sz50": "sh.000016",    # 上证50
    "hs300": "sh.000300",   # 沪深300
    "zz500": "sh.000905",   # 中证500
    "zz1000": "sh.000852",  # 中证1000
    "zz2000": "sh.932000",  # 中证2000
}


def main():
    parser = argparse.ArgumentParser(description="补充 K 线数据供回测使用")
    parser.add_argument("--start", default="2023-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="截止日期（默认今天）")
    parser.add_argument("--universe", default=None, choices=["hs300", "zz500", "sz50", "zz1000", "zz2000"], help="单个宇宙（与 --universes 二选一）")
    parser.add_argument("--universes", default=None, help="多个宇宙逗号分隔，如 sz50,hs300,zz1000；会依次补全并自动选用对应基准")
    parser.add_argument("--benchmark", default="sh.000852", help="基准代码（仅 --universe 时生效）")
    parser.add_argument("--sleep", type=float, default=0.02, help="请求间隔秒数")
    parser.add_argument("--max-retries", type=int, default=3, help="单次拉取失败时的重试次数")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="重试间隔秒数（指数退避）")
    parser.add_argument("-v", "--verbose", action="store_true", default=True, help="打印每只标的拉取日志")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式，仅打印进度")
    args = parser.parse_args()

    end = args.end or dt.date.today().isoformat()
    start = args.start

    if args.universes:
        universes = [u.strip() for u in args.universes.split(",") if u.strip()]
    elif args.universe:
        universes = [args.universe]
    else:
        universes = ["zz500"]  # 默认

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("BaoStock 登录失败：" + lg.error_msg)

    try:
        total_api, total_fail = 0, 0
        for uv in universes:
            benchmark = BENCHMARK_BY_UNIVERSE.get(uv, "sh.000905")
            print(f"\n补充 K 线: {start} ~ {end}  universe={uv}  benchmark={benchmark}", flush=True)

            uni = get_stock_list(uv, allow_bj=False)
            codes = uni["code"].tolist()
            codes.append(benchmark)
            codes = list(dict.fromkeys(codes))
            print(f"  待补充 {len(codes)} 只标的", flush=True)

            api_count, fail_count = supplement_kline(
                codes, start, end,
                sleep_s=args.sleep,
                max_retries=args.max_retries,
                retry_delay=args.retry_delay,
                verbose=not args.quiet,
            )
            total_api += api_count
            total_fail += fail_count
            print(f"  {uv} 完成，API {api_count} 次，失败 {fail_count} 只", flush=True)
        if len(universes) > 1:
            print(f"\n全部完成，共 API {total_api} 次，失败 {total_fail} 只", flush=True)
    finally:
        bs.logout()


if __name__ == "__main__":
    main()

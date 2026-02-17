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


def main():
    parser = argparse.ArgumentParser(description="补充 K 线数据供回测使用")
    parser.add_argument("--start", default="2023-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="截止日期（默认今天）")
    parser.add_argument("--universe", default="zz500", choices=["hs300", "zz500", "sz50", "zz1000", "zz2000"])
    parser.add_argument("--benchmark", default="sh.000852", help="基准代码")
    parser.add_argument("--sleep", type=float, default=0.02, help="请求间隔秒数")
    parser.add_argument("--max-retries", type=int, default=3, help="单次拉取失败时的重试次数")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="重试间隔秒数（指数退避）")
    parser.add_argument("-v", "--verbose", action="store_true", default=True, help="打印每只标的拉取日志")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式，仅打印进度")
    args = parser.parse_args()

    end = args.end or dt.date.today().isoformat()
    start = args.start

    print(f"补充 K 线: {start} ~ {end}", flush=True)
    print(f"  universe={args.universe}, benchmark={args.benchmark}", flush=True)

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("BaoStock 登录失败：" + lg.error_msg)

    try:
        uni = get_stock_list(args.universe, allow_bj=False)
        codes = uni["code"].tolist()
        codes.append(args.benchmark)
        codes = list(dict.fromkeys(codes))  # 去重保序
        print(f"  待补充 {len(codes)} 只标的", flush=True)

        api_count, fail_count = supplement_kline(
            codes, start, end,
            sleep_s=args.sleep,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            verbose=not args.quiet,
        )
        print(f"  完成，共发起 {api_count} 次 API 请求，失败 {fail_count} 只", flush=True)
    finally:
        bs.logout()


if __name__ == "__main__":
    main()

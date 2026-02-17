#!/bin/bash
# A股日频选股池 - 执行脚本

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "错误: 未找到 .venv，请先创建虚拟环境并安装依赖"
    exit 1
fi

case "${1:-}" in
    supplement)
        shift
        .venv/bin/python supplement_kline.py "$@"
        ;;
    backtest)
        shift
        .venv/bin/python backtest.py "$@"
        ;;
    *)
        .venv/bin/python run_daily.py "$@"
        ;;
esac

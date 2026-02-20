#!/bin/bash
# 每日选股启动脚本
# 用法: ./run_daily_selection.sh

# 工作目录
cd /Users/betta/work/python

# 激活虚拟环境
source .venv/bin/activate

# 日期
DATE=$(date +%Y-%m-%d)

echo "========================================"
echo "每日选股任务 - $DATE"
echo "========================================"

# 1. 检查是否开盘
echo ""
echo "[1/3] 检查开盘日..."
python scripts/check_market_open.py
if [ $? -ne 0 ]; then
    echo "今日不开市，跳过选股任务"
    exit 0
fi

# 2. 执行选股
echo ""
echo "[2/3] 执行选股任务..."
python scripts/run_stock_selection.py
if [ $? -ne 0 ]; then
    echo "选股任务执行失败"
    exit 1
fi

# 3. 生成报告
echo ""
echo "[3/3] 生成报告..."
python scripts/generate_report.py

echo ""
echo "========================================"
echo "选股任务完成 - $(date)"
echo "========================================"

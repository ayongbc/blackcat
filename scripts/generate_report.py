#!/usr/bin/env python3
"""
报告生成脚本
汇总各板块选股结果，生成markdown报告
"""

import os
import sys
from datetime import datetime

# 配置
WORK_DIR = "/Users/betta/work/python"
OUTPUT_DIR = os.path.join(WORK_DIR, "output")
DATE = datetime.now().strftime("%Y-%m-%d")

# 板块名称映射
UNIVERSE_NAMES = {
    "sz50": "上证50",
    "hs300": "沪深300", 
    "zz500": "中证500",
    "zz1000": "中证1000"
}

# 板块列表
UNIVERSES = ["sz50", "hs300", "zz500", "zz1000"]


def read_pool_csv(universe):
    """
    读取选股池CSV文件
    
    Args:
        universe: 板块名称
        
    Returns:
        list: 股票列表，每项为字典
    """
    file_path = os.path.join(OUTPUT_DIR, f"{DATE}_{universe}_pool.csv")
    
    if not os.path.exists(file_path):
        return []
    
    stocks = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        if len(lines) <= 1:
            return []
            
        # 解析CSV
        headers = lines[0].strip().split(',')
        
        for line in lines[1:]:
            if not line.strip():
                continue
                
            values = line.strip().split(',')
            if len(values) >= 2:
                stock = {}
                for i, h in enumerate(headers):
                    if i < len(values):
                        stock[h] = values[i]
                stocks.append(stock)
                
    except Exception as e:
        print(f"读取{universe}选股池失败: {str(e)}")
        
    return stocks


def read_report_md(universe):
    """
    读取选股报告Markdown文件
    
    Args:
        universe: 板块名称
        
    Returns:
        str: 报告内容
    """
    file_path = os.path.join(OUTPUT_DIR, f"{DATE}_{universe}_report.md")
    
    if not os.path.exists(file_path):
        return None
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"读取{universe}报告失败: {str(e)}")
        return None


def format_stock_list(stocks, limit=10):
    """
    格式化股票列表为markdown表格
    
    Args:
        stocks: 股票列表
        limit: 显示数量限制
        
    Returns:
        str: markdown格式表格
    """
    if not stocks:
        return "无入选股票"
    
    # 表头
    markdown = "| 排名 | 代码 | 名称 | 评分 | 形态 |\n"
    markdown += "|------|------|------|------|------|\n"
    
    # 数据行
    for i, stock in enumerate(stocks[:limit], 1):
        code = stock.get('code', '-')
        name = stock.get('name', '-')
        score = stock.get('score', stock.get('setup', '-'))
        setup = stock.get('setup', '-')
        
        markdown += f"| {i} | {code} | {name} | {score} | {setup} |\n"
    
    if len(stocks) > limit:
        markdown += f"\n*...还有 {len(stocks) - limit} 只股票*"
        
    return markdown


def generate_summary():
    """
    生成汇总报告
    
    Returns:
        str: markdown格式汇总报告
    """
    report = f"# 每日选股报告 - {DATE}\n\n"
    
    report += "## 执行信息\n\n"
    report += f"- **日期**: {DATE}\n"
    report += f"- **生成时间**: {datetime.now().strftime('%H:%M:%S')}\n"
    report += f"- **选股条件**: min_score ≥ 25.0, min_days_above_ma120 ≥ 30\n\n"
    
    # 各板块结果
    total_stocks = 0
    
    for universe in UNIVERSES:
        report += f"## {UNIVERSE_NAMES.get(universe, universe.upper())}\n\n"
        
        stocks = read_pool_csv(universe)
        
        if stocks:
            total_stocks += len(stocks)
            report += f"入选股票数: **{len(stocks)}只**\n\n"
            report += format_stock_list(stocks) + "\n\n"
        else:
            report += "无入选股票\n\n"
        
        report += "---\n\n"
    
    # 总结
    report += "## 汇总\n\n"
    report += f"- **总入选股票数**: {total_stocks}只\n"
    report += f"- **板块数**: {len(UNIVERSES)}\n\n"
    
    report += "## 备注\n\n"
    report += "- 选股条件: 评分≥25.0, 120日均线上天数≥30天\n"
    report += "- 数据来源: blackcat_py\n"
    report += "- 报告生成时间: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + "\n"
    
    return report


def save_report(content, filename=None):
    """
    保存报告到文件
    
    Args:
        content: 报告内容
        filename: 文件名，默认按日期命名
    """
    if filename is None:
        filename = f"{DATE}_daily_report.md"
        
    file_path = os.path.join(OUTPUT_DIR, filename)
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
        
    print(f"报告已保存到: {file_path}")
    return file_path


def main():
    """主函数"""
    print(f"\n{'#'*60}")
    print(f"# 生成每日选股报告")
    print(f"# 日期: {DATE}")
    print(f"{'#'*60}\n")
    
    # 生成汇总报告
    report = generate_summary()
    
    # 保存报告
    file_path = save_report(report)
    
    # 打印报告
    print(f"\n{'='*60}")
    print("报告内容:")
    print(f"{'='*60}\n")
    print(report)
    
    return file_path


if __name__ == "__main__":
    main()

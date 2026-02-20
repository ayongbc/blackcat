#!/usr/bin/env python3
"""
选股执行脚本
依次对指定板块执行选股任务
"""

import subprocess
import sys
import os
import time

# 工作目录
WORK_DIR = "/Users/betta/work/python"

# 板块列表（按顺序执行）
UNIVERSES = ["sz50", "hs300", "zz500", "zz1000"]

# 选股命令模板
# 使用 --auto --no-resume 参数确保完整执行
CMD_TEMPLATE = [
    "python", "run_daily.py",
    "--universe", "{universe}",
    "--auto", "--no-resume"
]


def run_selection(universe, timeout=600):
    """
    执行单个板块的选股任务
    
    Args:
        universe: 板块名称 (sz50/hs300/zz500/zz1000)
        timeout: 超时时间（秒），默认10分钟
        
    Returns:
        bool: True成功，False失败
    """
    print(f"\n{'='*50}")
    print(f"开始执行 {universe.upper()} 板块选股...")
    print(f"{'='*50}")
    
    # 构建命令
    cmd = [c.format(universe=universe) for c in CMD_TEMPLATE]
    
    try:
        # 执行命令
        result = subprocess.run(
            cmd,
            cwd=WORK_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        if result.returncode == 0:
            print(f"✓ {universe.upper()} 选股完成")
            # 打印最后几行输出
            output_lines = result.stdout.strip().split('\n')
            if output_lines:
                print("输出摘要:")
                for line in output_lines[-5:]:
                    print(f"  {line}")
            return True
        else:
            print(f"✗ {universe.upper()} 选股失败")
            print(f"错误输出: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"✗ {universe.upper()} 选股超时（{timeout}秒）")
        return False
    except Exception as e:
        print(f"✗ {universe.upper()} 选股异常: {str(e)}")
        return False


def run_all_selections():
    """
    依次执行所有板块的选股任务
    
    Returns:
        dict: {universe: success_bool}
    """
    results = {}
    
    # 切换到工作目录
    os.chdir(WORK_DIR)
    
    print(f"\n{'#'*60}")
    print(f"# 每日选股任务开始")
    print(f"# 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# 板块: {', '.join(UNIVERSES)}")
    print(f"{'#'*60}\n")
    
    # 依次执行每个板块
    for universe in UNIVERSES:
        success = run_selection(universe)
        results[universe] = success
        
        # 板块间休息一下（可选）
        if universe != UNIVERSES[-1]:
            print(f"等待 2 秒后执行下一个板块...")
            time.sleep(2)
    
    # 总结
    print(f"\n{'#'*60}")
    print(f"# 选股任务完成")
    print(f"{'#'*60}")
    
    success_count = sum(1 for v in results.values() if v)
    print(f"成功: {success_count}/{len(UNIVERSES)}")
    
    for universe, success in results.items():
        status = "✓ 成功" if success else "✗ 失败"
        print(f"  {universe.upper()}: {status}")
    
    return results


def run_single_universe(universe):
    """
    执行单个板块选股（用于测试）
    """
    os.chdir(WORK_DIR)
    return run_selection(universe)


if __name__ == "__main__":
    # 支持命令行参数
    if len(sys.argv) > 1:
        # 执行指定板块
        universe = sys.argv[1].lower()
        if universe in UNIVERSES:
            success = run_single_universe(universe)
            sys.exit(0 if success else 1)
        else:
            print(f"未知板块: {universe}")
            print(f"支持的板块: {', '.join(UNIVERSES)}")
            sys.exit(1)
    else:
        # 执行所有板块
        results = run_all_selections()
        
        # 返回状态码（全部成功为0，否则为1）
        all_success = all(results.values())
        sys.exit(0 if all_success else 1)

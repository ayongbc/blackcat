#!/usr/bin/env python3
"""
协调/编排员：自动化工作流
顺序执行 数据补全 → 选股 → 回测，并从 bt_config.yaml 读取参数。
"""

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


def load_config(path: str = "bt_config.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        raise RuntimeError("需要 PyYAML: pip install pyyaml")
    except Exception as e:
        raise RuntimeError(f"读取配置失败: {e}") from e


def run_step(name: str, cmd: list[str], retries: int = 1, retry_delay: float = 2.0) -> bool:
    """执行一步，失败时重试。返回是否成功。"""
    for attempt in range(retries + 1):
        if attempt > 0:
            print(f"  重试 ({attempt}/{retries})，{retry_delay}s 后执行...", flush=True)
            import time
            time.sleep(retry_delay)
        print(f"[{name}] {' '.join(cmd)}", flush=True)
        r = subprocess.run(cmd, cwd=Path(__file__).resolve().parent)
        if r.returncode == 0:
            return True
        print(f"[{name}] 退出码 {r.returncode}", flush=True)
    return False


def main():
    parser = argparse.ArgumentParser(description="自动化流水线：数据补全 → 选股 → 回测")
    parser.add_argument("--config", "-c", default="bt_config.yaml", help="配置文件路径")
    parser.add_argument("--skip-data", action="store_true", help="跳过数据补全")
    parser.add_argument("--skip-daily", action="store_true", help="跳过选股 run_daily")
    parser.add_argument("--skip-backtest", action="store_true", help="跳过回测")
    parser.add_argument("--retries", type=int, default=1, help="单步失败时的重试次数")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="重试间隔秒数")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    config = load_config(args.config)
    universe = config.get("universe", "zz500")
    benchmark_cfg = config.get("benchmark", "sh.000905")
    benchmark = (
        benchmark_cfg.get(universe) or benchmark_cfg.get("default") or "sh.000905"
        if isinstance(benchmark_cfg, dict)
        else (str(benchmark_cfg) if benchmark_cfg else "sh.000905")
    )
    start_date = config.get("start_date") or "2023-01-01"
    end_date = config.get("end_date")
    if end_date is None or end_date == "" or str(end_date).lower() == "null":
        end_date = dt.date.today().isoformat()
    else:
        end_date = str(end_date)

    python = sys.executable
    steps_ok = True

    # 1. 数据补全
    if not args.skip_data:
        if not run_step(
            "数据补全",
            [
                python, str(root / "supplement_kline.py"),
                "--start", start_date,
                "--end", end_date,
                "--universe", universe,
                "--benchmark", benchmark,
                "-q",
            ],
            retries=args.retries,
            retry_delay=args.retry_delay,
        ):
            steps_ok = False
            print("流水线在「数据补全」步骤失败，已停止。", flush=True)
            return 1
    else:
        print("[数据补全] 已跳过", flush=True)

    # 2. 选股
    if steps_ok and not args.skip_daily:
        if not run_step(
            "选股",
            [python, str(root / "run_daily.py"), "--universe", universe, "--auto"],
            retries=args.retries,
            retry_delay=args.retry_delay,
        ):
            steps_ok = False
            print("流水线在「选股」步骤失败，已停止。", flush=True)
            return 1
    elif not args.skip_daily:
        pass
    else:
        print("[选股] 已跳过", flush=True)

    # 3. 回测
    if steps_ok and not args.skip_backtest:
        if not run_step(
            "回测",
            [python, str(root / "backtest.py"), "--config", str(root / args.config)],
            retries=args.retries,
            retry_delay=args.retry_delay,
        ):
            steps_ok = False
            print("流水线在「回测」步骤失败。", flush=True)
            return 1
    elif not args.skip_backtest:
        pass
    else:
        print("[回测] 已跳过", flush=True)

    print("\n--- 流水线完成 ---", flush=True)
    print(f"  选股宇宙: {universe}, 基准: {benchmark}", flush=True)
    print(f"  回测区间: {start_date} ~ {end_date}", flush=True)
    print(f"  回测报告: output/backtest_report.md", flush=True)
    print("  下一步: 可由「评估与报告」员根据 output/backtest_report.md 撰写解读与改进建议。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

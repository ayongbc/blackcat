---
name: blackcat_py_select
description: Describes how to execute the project's stock-selection and backtest pipeline (data supplement, daily selection, backtest). Use when OpenClaw or any agent needs to run this repo's pipeline, run backtest, run daily selection, supplement K-line data, or interpret bt_config.yaml and output locations.
---

# 本项目执行说明（OpenClaw / Agent 使用）

本仓库为选股与回测流水线：**数据补全 → 选股 → 回测**。所有命令均在项目根目录执行。

## 环境

- Python 3，依赖见 `requirements.txt`（pandas, baostock, akshare, pyyaml）。
- 安装：`pip install -r requirements.txt`

## 一键流水线（推荐）

从配置读取宇宙、基准、回测区间，顺序执行：数据补全 → 选股 → 回测。

```bash
python run_pipeline.py
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--config FILE` | 配置文件，默认 `bt_config.yaml` |
| `--skip-data` | 跳过数据补全 |
| `--skip-daily` | 跳过选股 |
| `--skip-backtest` | 跳过回测 |
| `--retries N` | 单步失败重试次数，默认 1 |
| `--retry-delay SEC` | 重试间隔秒数，默认 2.0 |

示例：只跑回测（数据与选股已就绪）：

```bash
python run_pipeline.py --skip-data --skip-daily
```

## 分步执行

### 1. 数据补全（K 线）

保证回测区间内宇宙与基准的 K 线可用。

```bash
python supplement_kline.py --start 2024-01-01 --end 2024-03-31 --universe sz50 --benchmark sh.000852 -q
```

- `--universe`：`hs300` | `zz500` | `sz50` | `zz1000` | `zz2000`
- `-q`：静默；不加则打印每只标的日志

### 2. 选股（当日池与报表）

```bash
python run_daily.py --universe sz50 --auto
```

- `--universe`：同上；不传则需从配置或其它方式指定
- `--auto`：自动跑完所有批次；不加则单批
- `--no-resume`：不续跑，从头开始
- `--max-symbols N`：仅处理前 N 只（调试用）

### 3. 回测

```bash
python backtest.py --config bt_config.yaml
```

命令行可覆盖配置中的部分项：

- `--strategy NAME`：策略名（如 trend_ma）
- `--start YYYY-MM-DD` / `--end YYYY-MM-DD`：回测起止
- `--pool-screen-start` / `--pool-screen-end`：选股区间，不填则用 start/end

## 配置文件 `bt_config.yaml`

主要字段（供 OpenClaw 读/改时参考）：

| 字段 | 含义 |
|------|------|
| `strategy` | 策略名，如 trend_ma |
| `strategy_params` | 策略参数（池大小、过滤条件等） |
| `universe` | 选股宇宙：hs300/zz500/sz50/zz1000/zz2000 |
| `benchmark` | 基准指数代码，如 sh.000852 |
| `start_date` / `end_date` | 回测起止日期 |
| `initial_capital` | 初始资金（元） |
| `max_positions` | 最多持仓只数 |
| `adjustflag` | 复权：1=后复权，2=前复权，3=不复权 |

修改回测区间或宇宙后，若需补数据，应先跑 `supplement_kline` 或 `run_pipeline.py`（不跳过数据步）。

## 输出文件说明

所有产出均在项目根目录下；回测与选股结果在 `output/`，数据与中间状态在 `data/`。

### 回测产出（`output/`）

由 `backtest.py` 生成，**命名带时间戳**，格式为 `YYYYMMDD_HHMMSS`（运行时的日期时间），避免覆盖历史结果。

| 文件命名规则 | 说明 |
|--------------|------|
| `backtest_report_{YYYYMMDD_HHMMSS}.md` | 回测详细报告：策略/宇宙/区间、收益与风险汇总、日收益统计、月度收益、样本池与曲线引用、附带的 CSV 文件名 |
| `backtest_daily_{YYYYMMDD_HHMMSS}.csv` | 日频收益曲线。列：`date`, `daily_return`, `cumulative` |
| `backtest_trades_{YYYYMMDD_HHMMSS}.csv` | 全部交易明细。列：`hold_date`, `code`, `name`, `buy_date`, `sell_date`, `exit_reason`, `entry`, `exit_price`, `ret_pct`。无交易时可能不生成或为空 |

示例：`backtest_report_20260218_214443.md`、`backtest_trades_20260218_214443.csv`。每次运行回测会生成新的一组文件（新时间戳）。

### 选股产出（`output/`）

由 `run_daily.py` 生成，**命名含选股日期与宇宙**。

| 文件命名规则 | 说明 |
|--------------|------|
| `{YYYY-MM-DD}_{universe}_pool.csv` | 当日选股池（按评分取 top N）。列包含：`date`, `code`, `name`, `close`, `vol_ratio`, `avg_amt20`, `rs20`, `trend_open`, `atrp`, `score`, `setup` 等 |
| `{YYYY-MM-DD}_{universe}_report.md` | 当日选股报告：benchmark、universe、pool_size、过滤参数及池子表格（code/name/setup/score/rs20 等） |

示例：`2026-02-17_zz500_pool.csv`、`2026-02-17_zz500_report.md`。同一日期同一宇宙多次运行会覆盖当日文件。

### 数据与中间文件（`data/`）

| 路径/命名 | 说明 |
|-----------|------|
| `data/kline/{code}.csv` | K 线缓存。`code` 为交易所+代码，点号替换为下划线，如 `sh_601225.csv`、`sz_000703.csv` |
| `data/state.json` | 选股断点续跑状态（当前日期、进度等） |
| `data/partial_{YYYY-MM-DD}.csv` | 选股未完成时的中间结果（按日期） |

### 如何找到“最新”回测结果

回测文件按时间戳命名，最新一次运行对应时间戳最大。可通过 `output/backtest_report_*.md` 或 `output/backtest_trades_*.csv` 的修改时间或文件名中的 `YYYYMMDD_HHMMSS` 排序取最新。

## 角色与约定（可选参考）

项目内 Agent 角色与流水线顺序见仓库根目录 **AGENTS.md**。执行本仓库时只需按上述命令与配置即可；无需实现策略或数据逻辑，仅调用脚本并传参。

## 常见用法小结

- **完整跑一轮**：`python run_pipeline.py`
- **只补数据**：`python supplement_kline.py --start ... --end ... --universe ... -q`
- **只回测**：`python run_pipeline.py --skip-data --skip-daily` 或 `python backtest.py -c bt_config.yaml`
- **改区间/宇宙**：编辑 `bt_config.yaml` 的 `start_date`/`end_date`/`universe`，再跑流水线或对应步骤。

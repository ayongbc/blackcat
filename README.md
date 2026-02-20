# Blackcat 选股与回测

基于均线趋势与回踩形态的 A 股日频选股与回测流水线。配置驱动，支持多策略、多宇宙，与回测共用同一套筛选逻辑。

---

## 环境与依赖

- Python 3.10+
- 依赖：`pandas`、`baostock`、`pyyaml`、`tabulate`（报表 to_markdown）
- 选股宇宙 zz1000/zz2000 需安装：`pip install akshare`

安装核心依赖：

```bash
pip install pandas baostock pyyaml tabulate
```

---

## 目录结构

```
blackcat/
├── bt_config.yaml              # 默认回测/选股配置（策略1：回踩20均线）
├── bt_config_pullback_ma120.yaml # 策略2：回踩120均线专用配置
├── run_daily.py                 # 日频选股入口
├── backtest.py                  # 回测引擎
├── run_pipeline.py               # 自动化流水线：数据补全 → 选股 → 回测
├── supplement_kline.py          # K 线数据补全
├── data/
│   ├── kline_loader.py          # K 线加载、基准收益、补全
│   ├── universe.py              # 选股宇宙、交易日、成分股
│   └── kline/                   # 本地 K 线 CSV（按 code 分文件）
├── strategies/
│   ├── base.py                  # 策略基类
│   ├── trend_ma.py              # 策略1：回踩 MA20（trend_ma）
│   └── pullback_ma120.py        # 策略2：回踩 MA120（pullback_ma120）
└── output/                      # 选股池 CSV、日报 MD、回测报告与 CSV
```

---

## 配置文件说明

主配置为 **`bt_config.yaml`**（策略2 可选用 `bt_config_pullback_ma120.yaml`）。选股与回测共用该配置，保证逻辑一致。

| 配置项 | 说明 |
|--------|------|
| `strategy` | 策略名：`trend_ma`（回踩20均线）或 `pullback_ma120`（回踩120均线） |
| `strategy_params` | 策略参数（如 pool_size、min_avg_amount_20、min_days_above_ma120、止盈止损相关等） |
| `universe` | 选股宇宙：`hs300` / `zz500` / `sz50` / `zz1000` / `zz2000` |
| `benchmark` | 基准指数，可为按宇宙映射的字典或单一字符串 |
| `start_date` / `end_date` | 回测区间；选股日期可由命令行覆盖 |
| `pool_screen_start` / `pool_screen_end` | 选股时间段，仅此区间内每日重选池，不填则与回测区间一致 |
| `initial_capital` / `max_positions` | 资金与最多持仓只数 |
| `stop_loss_pct` / `take_profit_pct` / `break_ma20_pct` | 止盈止损与破 MA20 卖出 |
| `exit_by_score_rank` | 是否按当日选股池 score 前 N 调仓 |
| `hold_days` / `hold_strong_pct` | 弱势调仓：持有 N 日内未过买入价+X% 则调出 |

---

## 使用说明

### 1. 数据补全（K 线）

回测与选股依赖本地 `data/kline/` 下的 K 线。先对目标区间与宇宙补全数据。

**单宇宙：**

```bash
python supplement_kline.py --start 2023-01-01 --end 2025-02-20 --universe zz500
```

**多宇宙（自动选用各宇宙对应基准）：**

```bash
python supplement_kline.py --start 2023-01-01 --universes sz50,hs300,zz1000
```

常用参数：`--quiet` 静默、`--sleep` 请求间隔、`--max-retries` 重试次数。

---

### 2. 日频选股（run_daily）

按配置对**指定日期**做一次选股，输出当日候选池与日报。

**默认（最近交易日 + 默认配置）：**

```bash
python run_daily.py
```

**指定配置与日期：**

```bash
python run_daily.py --config bt_config.yaml --date 2025-02-20
```

**指定宇宙（覆盖配置文件）：**

```bash
python run_daily.py --config bt_config.yaml --universe zz500
```

**策略2（回踩120均线）：**

```bash
python run_daily.py --config bt_config_pullback_ma120.yaml
```

**调试（限制宇宙规模）：**

```bash
python run_daily.py --max-symbols 50
```

**输出文件（文件名含日期、宇宙、策略，避免覆盖）：**

- `output/{日期}_{宇宙}_{策略}_pool.csv`：当日候选池
- `output/{日期}_{宇宙}_{策略}_report.md`：日报摘要

示例：`output/2026-02-13_zz500_trend_ma_pool.csv`、`output/2026-02-13_zz500_trend_ma_report.md`。

---

### 3. 回测（backtest）

按配置在区间内逐日选池、模拟交易，输出收益与风险指标及报告。

**默认配置：**

```bash
python backtest.py --config bt_config.yaml
```

**指定区间：**

```bash
python backtest.py --config bt_config.yaml --start 2023-01-01 --end 2023-06-30
```

**覆盖策略与选股时间段：**

```bash
python backtest.py --config bt_config.yaml --strategy trend_ma --pool-screen-start 2023-01-01 --pool-screen-end 2023-03-31
```

**策略2：**

```bash
python backtest.py --config bt_config_pullback_ma120.yaml
```

**输出（带时间戳，不覆盖）：**

- `output/backtest_report_{时间戳}.md`：回测详细报告
- `output/backtest_daily_{时间戳}.csv`：日收益曲线
- `output/backtest_trades_{时间戳}.csv`：交易明细

---

### 4. 一键流水线（run_pipeline）

按顺序执行：**数据补全 → 选股 → 回测**，从同一份配置读取宇宙、基准、区间。

```bash
python run_pipeline.py --config bt_config.yaml
```

**跳过某几步：**

```bash
python run_pipeline.py --skip-data          # 不补 K 线，只选股+回测
python run_pipeline.py --skip-daily         # 不选股，只数据+回测
python run_pipeline.py --skip-backtest     # 不回测，只数据+选股
```

**重试：**

```bash
python run_pipeline.py --retries 2 --retry-delay 5
```

---

## 策略说明

| 策略 id | 说明 | 配置入口 |
|---------|------|----------|
| **trend_ma** | 回踩 20 均线：趋势过滤（MA5>MA20>MA60>MA120）、回踩 MA20 企稳、缩量、相对强度等 | `bt_config.yaml`（默认） |
| **pullback_ma120** | 回踩 120 均线：股价长期在 MA120 之上、回踩企稳、60MA>120MA、缩量等 | `bt_config_pullback_ma120.yaml` |

策略参数均在对应 YAML 的 `strategy_params` 中配置，回测与选股共用，保证一致。

---

## 命令行参数速查

**run_daily.py**

| 参数 | 说明 |
|------|------|
| `--config`, `-c` | 配置文件，默认 `bt_config.yaml` |
| `--date` | 选股日期 YYYY-MM-DD，默认最近交易日 |
| `--universe` | 覆盖配置中的宇宙 |
| `--pool-size` | 覆盖 config 的 pool_size |
| `--max-symbols` | 调试用，限制宇宙大小 |

**backtest.py**

| 参数 | 说明 |
|------|------|
| `--config`, `-c` | 配置文件 |
| `--strategy` | 覆盖策略名 |
| `--start` | 回测起始日期 |
| `--end` | 回测结束日期 |
| `--pool-screen-start` | 选股开始日 |
| `--pool-screen-end` | 选股结束日（此后沿用最后一日池子） |

**supplement_kline.py**

| 参数 | 说明 |
|------|------|
| `--start` / `--end` | 补全区间 |
| `--universe` | 单宇宙 |
| `--universes` | 多宇宙逗号分隔，如 `sz50,hs300,zz1000` |
| `--benchmark` | 基准代码（仅单宇宙时生效） |
| `-q` / `--quiet` | 静默模式 |

**run_pipeline.py**

| 参数 | 说明 |
|------|------|
| `--config`, `-c` | 配置文件 |
| `--skip-data` | 跳过数据补全 |
| `--skip-daily` | 跳过选股 |
| `--skip-backtest` | 跳过回测 |
| `--retries` | 单步失败重试次数 |
| `--retry-delay` | 重试间隔秒数 |

---

## 常见用法示例

```bash
# 1. 补全 zz500 的 2023 至今 K 线
python supplement_kline.py --start 2023-01-01 --universe zz500 -q

# 2. 用默认策略对最近交易日选股（hs300）
python run_daily.py

# 3. 用策略2 对 zz500 选股
python run_daily.py --config bt_config_pullback_ma120.yaml --universe zz500

# 4. 回测策略1，2023 年上半年
python backtest.py --config bt_config.yaml --start 2023-01-01 --end 2023-06-30

# 5. 一键：补数据 + 选股 + 回测（按 bt_config 区间与宇宙）
python run_pipeline.py --config bt_config.yaml
```

---

## 说明

- **T+1**：选股日不卖，选股次日开盘买入，持有至止盈/止损/破 MA20 或调仓规则触发。
- 选股与回测共用 **同一套策略与参数**（来自 YAML），保证结果可复现、可评估。
- 回测报告与 CSV 均带**时间戳**，历史结果不会互相覆盖；选股输出含**策略 id**，多策略同日期同宇宙也不会覆盖。

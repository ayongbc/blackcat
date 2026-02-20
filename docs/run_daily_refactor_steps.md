# run_daily.py 重构实现步骤

> 目标：单一事实源 + 单一配置源，日报与回测使用同一套策略与参数。

---

## 一、重构前对照（当前 vs 目标）

| 模块 | 当前 run_daily.py | 目标 |
|------|-------------------|------|
| 配置 | 内部 `CONFIG` 硬编码 | 读取 `bt_config.yaml`，命令行可覆盖 |
| 策略 | 内嵌 `score_one()` 约 80 行 | 调用 `strategies.get_strategy().screen()` |
| 数据 | `fetch_kline` / `load_or_update_kline` / `get_stock_list` 自实现 | 使用 `data.kline_loader.load_kline`、`data.universe.get_stock_list` |
| 基准 ret20 | `compute_benchmark_ret20()` 自实现 | 复用 backtest 的 `_compute_benchmark_ret20` 或抽取到公共函数 |
| 流水线 | 仅传 `--universe` | 传 `--config bt_config.yaml --date <date>` |

---

## 二、具体实现步骤

### 步骤 1：抽取基准收益率计算为公共函数

**目的**：`run_daily` 与 `backtest` 共用同一计算逻辑，避免参数/实现不一致。

**操作**：

1. 在 `data/kline_loader.py` 或新建 `data/benchmark.py` 中增加：

   ```python
   def compute_benchmark_ret20(
       benchmark: str, as_of_date: str, adjustflag: str = "2"
   ) -> float | None:
       """计算基准指数截至 as_of_date 的 20 日收益率"""
       from data.kline_loader import load_kline
       df = load_kline(benchmark, as_of_date, lookback_days=60, adjustflag=adjustflag)
       if df.empty or len(df) < 40:
           return None
       df = df.copy()
       df["ret20"] = df["close"].pct_change(20)
       last = df.dropna().tail(1)
       return float(last["ret20"].iloc[-1]) if not last.empty else None
   ```

2. `backtest.py` 中的 `_compute_benchmark_ret20` 改为调用该函数（或直接内联迁移到 `data`，backtest 调用它）。

**验收**：跑一次回测，benchmark_ret20 与之前一致。

---

### 步骤 2：为 run_daily 增加配置加载

**目的**：从 `bt_config.yaml` 读取 universe、benchmark、adjustflag、allow_bj、strategy、strategy_params 等。

**操作**：

1. 增加 `--config` 参数，默认 `bt_config.yaml`。
2. 使用 PyYAML 加载配置，构建 `config` 字典。
3. 配置优先级：命令行 > bt_config > 内置默认值。
4. 与策略相关的参数统一取自 `config["strategy_params"]`，并合并 `benchmark`、`adjustflag`、`allow_bj`、`universe` 等顶层配置。

**示例**：

```python
def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg

def build_strategy_config(cfg: dict) -> dict:
    """合并 strategy_params 与顶层配置，供策略使用"""
    params = cfg.get("strategy_params") or {}
    return {
        **params,
        "benchmark": cfg.get("benchmark", "sh.000852"),
        "adjustflag": str(cfg.get("adjustflag", "2")),
        "allow_bj": cfg.get("allow_bj", False),
        "universe": cfg.get("universe", "zz500"),
    }
```

---

### 步骤 3：删除 run_daily 中的重复实现

**目的**：移除与 `data/`、`strategies/` 重复的逻辑。

**删除或替换**：

| 当前函数/逻辑 | 操作 |
|--------------|------|
| `fetch_kline` | 删除，改用 `data.kline_loader.load_kline` |
| `load_or_update_kline` | 删除，改用 `data.kline_loader.load_kline` |
| `last_trading_date` | 删除，改用 `data.universe.get_trading_dates` 取最后一日，或 `data.universe._last_trading_date`（若导出） |
| `_akshare_code_to_baostock` | 删除，`data.universe` 已实现 |
| `get_universe_codes` | 删除，改用 `data.universe.get_universe_codes` |
| `get_stock_list` | 删除，改用 `data.universe.get_stock_list` |
| `compute_benchmark_ret20` | 删除，改用步骤 1 的公共函数 |
| `ma`, `atr_pct` | 删除，策略内部已有 |
| `score_one` | 删除，改用 `strategy.screen()` |

---

### 步骤 4：核心逻辑改为调用策略

**目的**：`run_daily` 只负责「取宇宙 → 取基准 → 调 strategy.screen → 写报表」。

**操作**：

1. 加载配置后，根据 `config["strategy"]` 获取策略实例：

   ```python
   from strategies import get_strategy
   StrategyClass = get_strategy(config["strategy"])
   strategy = StrategyClass(config=build_strategy_config(config))
   ```

2. 构建 `get_kline_fn`：

   ```python
   def get_kline_fn(code: str, end_date: str):
       return load_kline(
           code, end_date,
           lookback_days=config.get("lookback_days", 500),
           adjustflag=config.get("adjustflag", "2"),
       )
   ```

3. 获取宇宙、基准、选股日期：

   ```python
   end_date = args.date or _last_trading_date()  # 支持 --date 覆盖
   universe_df = get_stock_list(
       universe=config["universe"],
       query_date=end_date,
       allow_bj=config.get("allow_bj", False),
   )
   bench_ret20 = compute_benchmark_ret20(
       config["benchmark"], end_date, config.get("adjustflag", "2")
   )
   if bench_ret20 is None:
       raise RuntimeError("基准指数数据不足")
   ```

4. 调用策略选股：

   ```python
   rows = strategy.screen(
       date=end_date,
       universe_df=universe_df,
       get_kline_fn=get_kline_fn,
       benchmark_ret20=bench_ret20,
   )
   ```

5. 将 `rows`（list[dict]）转为 DataFrame，写 CSV 和 report.md。

---

### 步骤 5：保留断点续跑能力（可选）

**目的**：宇宙很大时，一次跑完可能超时，支持分批续跑。

**分析**：`TrendMAStrategy.screen()` 一次性遍历整个 universe，无分批接口。两种方案：

- **方案 A（推荐）**：暂不实现分批，先完成“单一事实源”。若宇宙很大，由 `supplement_kline` 先补数据，再跑选股；或用 `strategy_params.pool_workers > 1` 多进程加速。
- **方案 B**：在 `run_daily` 层做分批（按 universe 切片），每批调用 `strategy.screen(universe_df=chunk)`，合并结果后排序取 top N，并支持 `--batch-size`、`--resume`。这会增加复杂度，建议在 A 跑通后再加。

**本阶段**：采用方案 A，删除原 `run_batch`、`partial_*.csv`、`state.json` 的断点续跑逻辑；若后续需要，再在策略或 run_daily 层加分批接口。

---

### 步骤 6：调整 write_reports

**目的**：报表内容与回测使用的参数一致，来源自 `config`。

**操作**：

1. `write_reports(end_date, pool_df, config)` 从 `config` 读取 benchmark、universe、pool_size、min_score 等写进 report。
2. CSV 列保持与 `strategy.screen()` 返回的 dict 一致（code, name, score, setup, rs20, vol_ratio, atrp, avg_amt20, close 等）。
3. 若策略返回的字段有变化，报表只展示存在的列，用 `pool_df.columns` 动态生成。

---

### 步骤 7：命令行与 run_pipeline 集成

**目的**：流水线传 config 和 date，run_daily 与回测使用同一配置。

**run_daily 新增参数**：

```text
--config, -c     default: bt_config.yaml
--date           default: 最近交易日（用于指定选股日）
--universe       覆盖 config 中的 universe
--pool-size      覆盖 config 中的 pool_size
--max-symbols    调试用，限制宇宙大小
```

**run_pipeline 修改**：

```python
# 第 2 步选股
cmd = [
    python, str(root / "run_daily.py"),
    "--config", str(root / args.config),
    "--date", end_date,
    "--auto",  # 若保留 auto 语义，否则可去掉
]
if universe:
    cmd.extend(["--universe", universe])
run_step("选股", cmd, ...)
```

注意：重构后 `--auto` 可能不再需要（不再分批），可简化为直接执行一次选股。

---

### 步骤 8：数据目录与 kline 路径统一

**目的**：run_daily 与 data/kline_loader 使用同一 kline 目录。

**现状**：`data/kline_loader.py` 使用 `os.path.join(os.path.dirname(__file__), "..", "data", "kline")`，即项目下的 `data/kline/`。run_daily 原用 `DATA_DIR = "data"`，路径为 `data/kline/`，一致。

**操作**：删除 run_daily 中的 `ensure_dirs()` 对 kline 目录的创建（`data/kline_loader` 的 `load_kline` 内部会 `_ensure_kline_dir()`）。保留 `os.makedirs(OUT_DIR, exist_ok=True)` 即可。

---

### 步骤 9：BaoStock 登录收敛

**目的**：避免 run_daily 与策略内部重复登录。

**操作**：

1. 在 `run_daily.main()` 开头调用一次 `bs.login()`，结束时 `bs.logout()`。
2. 当 `pool_workers == 0` 时，`TrendMAStrategy.screen` 使用主进程的 `get_kline_fn`，`load_kline` 内部不登录；BaoStock 通常允许同一进程重复 login，但保留一次即可。
3. 若 `pool_workers > 1`，子进程内会各自 `bs.login()`（`_score_one_stock` 已实现），无需改。

---

### 步骤 10：对照验证

**目的**：确保重构前后同一日期、同一宇宙、同一参数下，选股结果一致。

**操作**：

1. 选一个交易日，如 `2025-02-14`（或 config 中 end_date 之前的某个交易日）。
2. 备份当前 `run_daily` 输出：`output/2025-02-14_zz500_pool.csv`。
3. 修改 `bt_config.yaml` 的 `strategy_params`，使与旧 `run_daily.CONFIG` 完全一致（pool_size、min_score 等）。
4. 执行重构后的 `run_daily.py --config bt_config.yaml --date 2025-02-14`。
5. 对比新旧 `pool.csv`：top N 的 code 及 score 应一致（允许浮点微小差异）。
6. 再跑一次 `backtest.py --config bt_config.yaml`，确认回测在对应日期的选股池与日报一致。

---

## 三、文件改动清单

| 文件 | 改动 |
|------|------|
| `run_daily.py` | 大幅精简：删重复逻辑，改为调用 data + strategies，从 config 加载 |
| `data/benchmark.py`（新建）或 `data/kline_loader.py` | 新增 `compute_benchmark_ret20` |
| `backtest.py` | `_compute_benchmark_ret20` 改为调用 data 层函数（若抽取） |
| `run_pipeline.py` | 选股步骤传入 `--config`、`--date` |
| `bt_config.yaml` | 若需支持 run_daily 的 `lookback_days`，可在 strategy_params 中增加（可选） |

---

## 四、实施顺序建议

1. **步骤 1**：抽取 benchmark_ret20，保证 backtest 行为不变。
2. **步骤 2 + 4**：先实现“从 config 读 + 调 strategy.screen”，暂保留旧数据函数，验证能跑通。
3. **步骤 3**：再删 run_daily 内重复实现，全面切换到 data 层。
4. **步骤 5 ~ 9**：清理断点续跑、报表、命令行、run_pipeline。
5. **步骤 10**：做对照验证。

---

## 五、风险与回退

- **风险**：断点续跑移除后，大宇宙单次运行时间可能变长。可通过 `pool_workers` 或多进程缓解。
- **回退**：重构前为 `run_daily.py` 打 tag 或建分支，验证失败时可回滚。

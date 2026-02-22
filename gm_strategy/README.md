# 掘金版回踩120均线策略

本目录为**掘金量化**平台独立策略，与仓库内 `strategies/pullback_ma120.py` 及 `bt_config_pullback_ma120.yaml` 逻辑一致，仅依赖 `gm`（掘金 SDK）和 `pandas`，不依赖本仓库的 baostock/akshare/data.*。

## 依赖

```bash
pip install gm pandas
```

## Token 配置

**不要将 token 写死在代码中提交。**

- 方式一：在脚本内将 `GM_TOKEN = os.environ.get("GM_TOKEN", "your_token_here")` 里的 `your_token_here` 改为你的 token（仅本地使用，勿提交）。
- 方式二：设置环境变量后直接运行：
  ```bash
  set GM_TOKEN=你的token
  python gm_pullback_ma120.py
  ```

## 运行方式

### 本地用掘金 SDK 回测

```bash
cd gm_strategy
python gm_pullback_ma120.py
```

脚本内 `run()` 会使用 `DEFAULT_CONFIG` 中的 `backtest_start`、`backtest_end`、`initial_capital` 等参数；可在文件顶部修改这些常量。

### 在掘金终端中运行

1. 将 `gm_strategy/` 下文件拷贝到掘金策略目录。
2. 在终端中 `run(..., filename='gm_pullback_ma120.py')`，token 可由终端绑定计算机 ID，或在 run 参数中传入。

## 策略逻辑概要

- **选股**：中证500成分股，回踩120均线（与 `pullback_ma120` 相同过滤与评分），取 score 前 `pool_size` 只，结果存为「次日待买」列表。
- **执行**：每日 09:35 先根据「昨日」待买列表市价买入（按资金均分），再对持仓做止盈/止损/破 MA20/弱势调仓检查并卖出，最后用昨日收盘数据重新选股，更新「次日待买」列表。
- **止盈止损**：`stop_loss_pct`、`take_profit_pct`、`break_ma20_pct`（与 yaml 一致，脚本内为小数形式）。
- **弱势调仓**：持有满 `hold_days` 日且该段时间内最高价未超过买入价 × (1 + `hold_strong_pct`)，则卖出。
- **回测结束日**：对剩余持仓市价平仓。

## 宇宙与基准

- 当前仅支持 **zz500（中证500）**：成分股通过掘金 `get_history_constituents(index='SHSE.000905', ...)` 获取。
- 基准与宇宙一致：`benchmark_symbol = 'SHSE.000905'`（中证500），用于计算选股时的相对强度 rs20。

若掘金某指数代码与上述不一致，需在代码中修改 `universe_index` 与 `benchmark_symbol`（建议二者使用同一指数），并参考掘金文档确认历史成分股接口可用性。

## 参数

主要参数集中在 `DEFAULT_CONFIG`（与 `bt_config_pullback_ma120.yaml` 对齐），包括：

- `pool_size`、`max_positions`、`min_days_above_ma120`、`pullback_lookback`、`volume_shrink_ratio` 等选股参数；
- `stop_loss_pct`、`take_profit_pct`、`break_ma20_pct`、`hold_days`、`hold_strong_pct` 等风控与调仓参数；
- `backtest_start`、`backtest_end`、`initial_capital` 等回测参数。

可直接在 `gm_pullback_ma120.py` 顶部修改，或后续扩展为从同目录 YAML/JSON 读取。

---

## 单只股票基本面查询（query_fundamentals.py）

用于**查询单只股票的详细基本面**（资产负债表、利润表、现金流量表、财务主要指标），仅调用掘金数据接口，无需策略框架。

### 掘金基本面接口说明

掘金提供四类**截面**基本面接口（point-in-time，需先 `set_token` 再调用）：

| 接口 | 说明 | 常用参数 |
|------|------|----------|
| `stk_get_fundamentals_balance_pt` | 资产负债表 | symbols, fields, date, df=True |
| `stk_get_fundamentals_income_pt` | 利润表 | 同上 |
| `stk_get_fundamentals_cashflow_pt` | 现金流量表 | 同上 |
| `stk_get_finance_prime_pt` | 财务主要指标（ROE、EPS、每股净资产等） | 同上 |

- **symbols**：单只 `'SHSE.600000'` 或多只 `'SHSE.600000,SZSE.000001'`
- **date**：查询日期 `YYYY-MM-DD`，`None` 表示最新可用；返回的是「发布日期 ≤ date」的最新报告
- **fields**：逗号分隔的字段名，单次最多 20 个，字段列表见掘金文档 [股票财务数据及基础数据函数](https://www.myquant.cn/docs2/sdk/python/API介绍/股票财务数据及基础数据函数（免费）.html)
- **rpt_type**：报表类型 1=一季报、6=中报、9=三季报、12=年报，不传则不限
- **data_type**：102=合并调整（推荐），101=合并原始等

### 用法

```bash
# 设置 token 后执行（与策略共用 GM_TOKEN）
set GM_TOKEN=你的token

# 查询单只股票（支持 6 位代码，自动区分沪/深）
python gm_strategy/query_fundamentals.py SHSE.600000
python gm_strategy/query_fundamentals.py 600000
python gm_strategy/query_fundamentals.py SZSE.000001 --date 2024-06-01
```

脚本会输出：财务主要指标、利润表摘要、资产负债表摘要、现金流量摘要。

**扩展字段**：可通过参数传入，支持覆盖或追加（掘金单次最多约 20 个字段）：

- `--balance-fields`、`--income-fields`、`--cashflow-fields`、`--prime-fields`、`--deriv-fields`：逗号分隔的字段名。
- 不传则使用脚本内置默认字段。
- **以 `+` 开头**表示在默认字段后追加，例如：`--balance-fields "+goodwill,oth_ast"`。
- 不以 `+` 开头则用该字符串**整体替换**默认字段。
- **字段中文注释**：见同目录 [财务字段说明.md](财务字段说明.md)，含资产负债表、利润表、现金流量表、财务主要指标、财务衍生指标等字段的中文名称与量纲。

```bash
python gm_strategy/query_fundamentals.py 600089 --balance-fields "+goodwill,oth_ast"
```

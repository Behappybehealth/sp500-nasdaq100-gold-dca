# 全样本滚动定投回测模型实施计划

## Context

用户已经用 `C:\Users\xiezhibo\generate_sp500_dca.py` 生成了 2016、2021、2024 三个固定起点的标普 500 每日定投明细，并核对了行数、本金和期末市值。三个固定起点只能说明少数样本表现，不能回答“任意交易日开始、投资任意周期时，收益、回撤、亏损概率和资金年化收益如何变化”。

本次改造目标是把现有脚本扩展为可复用的全样本滚动起点/滚动周期定投回测模型：每个交易日都作为起投日，测试 3 个月、6 个月、1 年、2 年、3 年、5 年、7 年、10 年等周期，并输出两类核心文件：逐窗口明细 CSV 和按周期汇总 CSV/报告。实现时所有组合计算都在本地 Python 循环中完成，避免把每个起点/周期拆成大量工具调用，从根本上规避用户提到的 `tool_calls` 数组超过 128 的错误。

## Critical files

- 修改：`C:\Users\xiezhibo\generate_sp500_dca.py`
- 复用现有输出目录：`C:\Users\xiezhibo\sp500_dca_outputs\`
- 新增输出：
  - `sp500_dca_rolling_windows_to_YYYYMMDD.csv`
  - `sp500_dca_period_summary_to_YYYYMMDD.csv`
  - `sp500_dca_rolling_report_to_YYYYMMDD.md`
- 保留现有输出：
  - 三个固定起点明细 CSV
  - `sp500_dca_summary_to_YYYYMMDD.csv`
  - `sp500_dca_report_to_YYYYMMDD.md`

## Existing code to reuse

- Yahoo Finance 下载逻辑：`generate_sp500_dca.py` 顶部第 13-33 行，改造成 `download_prices(start, end)`。
- 单一起点定投明细：`build_dca(start_date)`，保留用于生成 2016/2021/2024 三个样例明细。
- XIRR 求解：`xirr(cashflows)`，保留并增强边界检查。
- 明细 CSV 格式化：`write_csv(path, data)`，继续用于老的 detail 文件；新增通用 `write_dict_csv(...)` 写滚动结果。
- 原有三场景汇总：`summary_for(label, start_date)` 可继续保留，方便对照历史结果。

## Recommended implementation

### 1. 重构下载逻辑

把当前顶层 Yahoo 下载代码封装为：

```python
def download_prices(start, end):
    ...
    return rows
```

`rows` 仍为按日期升序排列的列表：

```python
{"date": datetime.date, "close": float}
```

增加基础校验：

- 数据非空；
- 日期升序；
- 无重复交易日；
- 收盘价均大于 0。

### 2. 增加滚动周期配置

新增常量：

```python
ROLLING_PERIODS = ["3m", "6m", "1y", "2y", "3y", "5y", "7y", "10y"]
```

新增 `add_period(start_date, period)`：

- `3m`、`6m` 按自然月增加；
- `1y`、`2y`、`3y`、`5y`、`7y`、`10y` 按自然年增加；
- 月末日期越界时自动夹到目标月份最后一天，例如 1 月 31 日 + 1 个月变为 2 月 28/29 日；
- 只使用 Python 标准库，不引入第三方依赖。

### 3. 确定滚动窗口边界

对每个 `start_idx` 和 `period`：

1. `start_date = dates[start_idx]`；
2. `target_end_date = add_period(start_date, period)`；
3. 若 `target_end_date > dates[-1]`，跳过，避免不完整周期；
4. 用 `bisect.bisect_right(dates, target_end_date) - 1` 找到不晚于目标日期的最后一个交易日作为实际结束日；
5. 要求 `end_idx >= start_idx`。

需要在报告中明确口径：周期按自然日/月/年计算，实际结束日取目标日期当日或之前最近交易日，数据尾部不足完整周期的窗口不纳入统计。

### 4. 使用数组和前缀和提升性能

把价格数据转为紧凑数组：

```python
dates = [r["date"] for r in rows]
closes = [r["close"] for r in rows]
prefix_inv_close[i + 1] = prefix_inv_close[i] + 1.0 / closes[i]
```

任意窗口 `[start_idx, end_idx]` 的期末份额和市值用前缀和快速计算：

```python
trading_days = end_idx - start_idx + 1
principal = DAILY_INVEST_RMB * trading_days
shares = DAILY_INVEST_RMB * (prefix_inv_close[end_idx + 1] - prefix_inv_close[start_idx])
ending_value = shares * closes[end_idx]
profit = ending_value - principal
cumulative_return = profit / principal
```

### 5. 计算每个滚动窗口指标

新增 `compute_window_metrics(...)`，返回一行字典，字段建议使用便于后续处理的英文列名，百分比以小数保存，例如 `0.1234` 表示 `12.34%`。

逐窗口输出字段：

- `period`
- `start_date`
- `end_target_date`
- `end_date`
- `trading_days`
- `principal_rmb`
- `ending_value_rmb`
- `profit_rmb`
- `cumulative_return`
- `xirr`
- `max_drawdown`
- `longest_recovery_days`
- `start_sp500`
- `end_sp500`
- `index_return`

XIRR 使用现有 `xirr`，现金流为窗口内每个交易日 `-100`，窗口结束日追加 `+ending_value`。

### 6. 计算最大回撤和最长回本/修复时间

新增 `compute_path_risk_metrics(dates, closes, start_idx, end_idx)`。

窗口内按现有定投逻辑逐日模拟：

```python
shares_so_far += DAILY_INVEST_RMB / closes[i]
value = shares_so_far * closes[i]
```

最大回撤口径：账户市值从历史高点到后续低点的最大跌幅：

```python
drawdown = value / peak_value - 1
max_drawdown = min(max_drawdown, drawdown)
```

最长修复时间口径：账户市值跌破前高后，重新达到或超过该前高所需的最长自然日数；如果到窗口结束仍未修复，则从回撤开始日统计到窗口结束日。

报告中补充说明：由于定投持续注入本金，账户市值回撤可能低估真实心理压力；后续如需要，可以再增加“收益曲线回撤”或“单位净值回撤”。本次先按账户市值最大回撤实现。

### 7. 汇总每个周期的统计表

新增 `summarize_by_period(window_rows)`，按 `period` 分组，输出用户要求的最终表，并增加若干辅助列。

核心字段：

- `period`
- `window_count`
- `start_min`
- `start_max`
- `best_return`
- `median_return`
- `worst_return`
- `loss_probability`
- `worst_max_drawdown`
- `median_max_drawdown`
- `median_xirr`
- `worst_xirr`
- `best_xirr`
- `max_recovery_days`
- `median_recovery_days`

其中：

- `loss_probability = cumulative_return < 0 的窗口数 / window_count`；
- `worst_return / median_return / best_return` 基于 `cumulative_return`；
- 用户关心的“最大回撤”在汇总表中用 `worst_max_drawdown` 表示，即所有同周期窗口里最糟糕的最大回撤；
- `XIRR 中位数` 用 `median_xirr`；
- 忽略 `None` 的 XIRR 值后再计算 XIRR 统计。

### 8. 输出文件

#### 逐窗口 CSV

`sp500_dca_rolling_windows_to_YYYYMMDD.csv`

保存每个有效 `(start_date, period)` 组合的完整指标，供用户后续自行筛选、透视和画图。

#### 周期汇总 CSV

`sp500_dca_period_summary_to_YYYYMMDD.csv`

保存用户要求的最终矩阵：3 个月、6 个月、1 年、2 年、3 年、5 年、7 年、10 年分别对应最好收益率、中位数收益率、最差收益率、亏损概率、最大回撤、XIRR 中位数等。

#### Markdown 报告

`sp500_dca_rolling_report_to_YYYYMMDD.md`

包含：

- 数据源、周期口径、定投金额、费用/汇率/税费假设；
- 用户要求的最终汇总表，百分比格式展示；
- 对“任意时间开始投，胜率是多少、最差会亏多少、投多久更稳、是否适合加杠杆”的解释入口；
- 输出文件路径和逐窗口 CSV 的字段说明。

### 9. 避免 `tool_calls` array too long` 的措施

- 不为每个起点、周期、明细文件发起工具调用；
- 不用外部工具逐个处理窗口；
- 只运行一次 Python 脚本，脚本内部用本地循环完成所有窗口计算；
- 输出少数几个聚合文件，而不是为每个起点生成一个文件；
- 验证时只读取摘要、前几行或用简单行数统计，不把全量 CSV 内容加载进对话。

## Verification

实施后执行：

```bash
python C:/Users/xiezhibo/generate_sp500_dca.py
```

需要验证：

1. 脚本正常结束并打印数据行数、输出目录和新增文件路径。
2. 新增文件存在：
   - `sp500_dca_rolling_windows_to_20260805.csv`
   - `sp500_dca_period_summary_to_20260805.csv`
   - `sp500_dca_rolling_report_to_20260805.md`
3. 原有三个固定起点的结果仍与用户已核对数据一致：
   - 2016 起：2661 行，累计本金 266100，期末市值约 594248.995214；
   - 2021 起：1402 行，累计本金 140200，期末市值约 222106.202693；
   - 2024 起：649 行，累计本金 64900，期末市值约 83377.983162。
4. 汇总 CSV 每个周期满足：
   - `window_count > 0`；
   - `loss_probability` 在 `[0, 1]`；
   - `worst_return <= median_return <= best_return`；
   - `worst_max_drawdown <= 0`；
   - `median_xirr` 非空或有合理空值解释。
5. 抽取一个窗口与现有 `build_dca(start_date)` 的切片结果对照：本金、期末市值、累计收益率和 XIRR 应一致或仅有浮点误差。
6. 报告中的周期表能直接回答用户指定问题：任意起点胜率/亏损概率、最差收益、最长修复时间、最大回撤和 XIRR 中位数。

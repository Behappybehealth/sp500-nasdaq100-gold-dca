# sp500-nasdaq100-gold-dca 完整升级计划

## Context

原窗口无法继续，是因为一次 API 请求里累计发起了 129 个 `tool_calls`，超过单次最多 128 个工具调用的限制。问题不在 DCA 策略或 Python 脚本，而在执行方式：把大量 Todo、Read、Agent、Write 等工具调用一次性并行发送了。

本次要按用户给出的总览差异表，把 `sp500-nasdaq100-gold-dca` 从偏“按金额算分配”的工具升级为完整的每日动态定投决策助手。固定流程为：**复盘 → 持仓 → 预算 → 行情 → 决策 → 记录**。每日输出必须回答：今天买不买、买多少、怎么分配，并展示上一条记录、累计持仓宽表、自然月预算、最新行情、XIRR、估值/盈亏和记录模板。

实施阶段必须避免再次出现 `tool_calls array too long`：每轮只使用少量工具调用；不使用大型 `multi_tool_use.parallel`；不把每个文件、检查项、行情点拆成独立工具调用；批量计算、行情缓存更新、宽表、XIRR 和验证摘要全部由本地 Python 脚本一次完成，只读取最终关键输出。

## Critical files

将修改以下文件：

- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\data\config.json`
- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\requirements.txt`
- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\scripts\dca_calculator.py`
- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\SKILL.md`
- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\strategy\core-strategy.md`

保留且不主动覆盖历史记录，只在用户确认后追加：

- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\data\transactions.csv`
- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\data\observations.csv`

脚本会新增/使用行情缓存目录：

- `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\data\market_history\*.csv`

## Existing code and patterns to reuse

从当前 `scripts/dca_calculator.py` 复用并扩展：

- `read_transactions()`：读取真实成交记录。
- `read_last_observation()`：读取观察/跳过记录。
- `monthly_budget_status()`：自然月预算框架，扩展为月末投完压力。
- `fetch_history()` / Yahoo Chart fallback：保留 yfinance 优先 + Yahoo Chart fallback。
- `metrics_from_closes()`：复用收益率、均线、波动、回撤、252 日区间位置等指标。
- `portfolio_summary()`：扩展为每资产 + 组合估值、盈亏、权重、XIRR。

从旧 `investment-dca` skill 的实现思路复用：

- XIRR 的 `xnpv` / `xirr` 二分求解逻辑。
- 脚本直接输出宽表数据，而不是只输出基础 JSON 后让模型临时拼表。

## Recommended implementation

### 1. 升级配置

更新 `data/config.json`：

- 默认买入范围仍只包含三类资产，不把杠杆或其他品种加入默认买入比例。
- 资产定位统一为：标普500 = 主仓 / 核心权益；纳指100 = 辅仓 / 成长弹性；黄金 = 独立防御 / 分散抗通胀。
- 为满足“收益最大化、可接受较大浮亏和更长持有周期”，同时避免当前“配置 40/50/10、文档 45/35/20”的冲突，统一为：标普500 50%、纳指100 35%、黄金 15%。标普仍是主仓，纳指保留更宽弹性区间。
- 动态区间：标普500 30%~65%；纳指100 20%~60%；黄金 5%~30%，极端风险环境只观察到 35%，不默认长期超配。
- 动态金额档位：`0 / 1500 / 3000 / 5000 / 7000 / 10000`。
- 月度预算：30000 RMB，按自然月统计，加入月末释放参数，尽量自然月内投完。
- 行情缓存参数：缓存目录、历史年限、允许缓存滞后天数、增量刷新设置。
- 边界参数：杠杆仅观察提醒；其他品种仅观察资产，不擅自加入每日买入比例。

### 2. 重写 `dca_calculator.py`

保留 CLI 入口并扩展参数：

- `--amount`：用户本次参考金额。
- `--base-dir`：skill 根目录。
- `--history-years`：历史数据年限。
- `--no-refresh`：只用本地缓存验证格式或离线运行。
- `--format json|markdown`：默认 JSON，便于 skill 消费。

新增能力：

1. **行情缓存与增量更新**
   - 首次运行拉取历史日线，保存到 `data/market_history/{symbol}.csv`。
   - 后续先读缓存，只从缓存最后日期之后增量刷新。
   - yfinance 优先，Yahoo Chart fallback；网络失败时使用缓存并标注 `cache_only` / `stale_cache`。
   - 输出每个标的的数据源、缓存状态、历史起止日期、点数、最新价格、日涨跌。

2. **动态决策**
   - 计算趋势、回撤、位置、动量、波动等信号。
   - 标普保持主仓，纳指作为辅仓弹性，黄金作为独立防御。
   - 根据信号选择今日总金额档位：0、1500、3000、5000、7000、10000。
   - 越接近月末且剩余预算越多，越倾向提高金额档位；但在明显高位 / 过热时不机械追高。
   - 分配比例在配置区间内动态调整，总额不超过自然月剩余预算，除非用户明确追加预算。

3. **累计持仓、估值、盈亏和 XIRR**
   - 每资产计算累计投入、份额、当前估值、未实现盈亏、收益率、组合权重。
   - 组合层计算总投入、总估值、总盈亏、总收益率。
   - 买入为负现金流，卖出为正现金流，当前估值作为最后一笔正现金流，计算每资产和组合年化 XIRR。
   - 无交易历史或现金流不足时输出 `null`，回答中显示“暂无”。

4. **宽表输出**
   - JSON 中增加 `wide_table.rows`，脚本直接生成完整宽表数据。
   - 行至少包含：组合汇总、标普500、纳指100、黄金、现金 / 待投预算。
   - 字段覆盖：层级、日期、期数、资产、上一条记录类型、上一条投入、本月已投、自然月剩余预算、累计投入、持仓份额、最新价格、当前估值、未实现盈亏、收益率、年化 XIRR、组合权重、今日建议金额、今日建议比例、备注。

5. **记录辅助输出**
   - 脚本不直接写入交易或观察记录。
   - 输出 `recording_templates`：买入成交模板和跳过观察模板。
   - skill 回答中必须先复述待写入字段，等待用户确认后才追加 CSV。

### 3. 更新 `SKILL.md`

同步为每日执行规范：

- 明确核心定位：动态定投决策助手，不是固定金额分配器。
- 固定回答顺序：一句话结论 → 风险边界 → 上一条记录 → 累计持仓宽表 → 自然月预算 → 最新行情 → 今日决策 → 三档方案 → 记录确认。
- 明确风险表述：无法保证不亏，也不能保证实际本金不亏；只能通过分批、分散和估值 / 趋势信号降低永久亏损概率。
- 明确用户可接受较大浮亏和长周期，因此在回撤性价比较好时可更积极释放预算。
- 明确每月 30000 RMB 尽量自然月内投完。
- 明确动态金额档位和月末资金调度。
- 明确每天抓最新行情，但使用本地缓存与增量更新。
- 明确必须展示上一条记录、完整宽表、XIRR、每资产和组合估值 / 盈亏。
- 明确杠杆只做严格条件下观察提醒，不默认加入买入比例。
- 明确其他品种只作为观察资产，不能擅自加入三资产买入分配。

### 4. 更新 `core-strategy.md`

同步策略文档：

- 与 `config.json` 的权重、区间、金额档位完全一致。
- 强化“标普主仓、纳指辅仓、黄金独立防御”。
- 增加收益最大化倾向与更大浮亏承受假设。
- 增加月末剩余预算释放机制。
- 增加行情缓存、增量更新、缓存失效处理。
- 增加 XIRR 口径说明。
- 增加杠杆和其他观察资产边界。

### 5. 更新 `requirements.txt`

保持轻量依赖：

- `yfinance>=0.2.40`
- `pandas>=2.0.0`
- `numpy>=1.24.0`
- `requests>=2.31.0`
- `python-dateutil>=2.8.2`

XIRR 在脚本内实现，不引入额外财务库。

## Verification

实施完成后只用少量命令验证：

1. 安装 / 确认依赖：
   - `python -m pip install -r C:/Users/xiezhibo/.claude/skills/sp500-nasdaq100-gold-dca/requirements.txt`
2. 首次运行并刷新行情：
   - `python C:/Users/xiezhibo/.claude/skills/sp500-nasdaq100-gold-dca/scripts/dca_calculator.py --amount 5000 --base-dir C:/Users/xiezhibo/.claude/skills/sp500-nasdaq100-gold-dca`
3. 二次运行验证缓存 / 增量：
   - 再运行同一命令，确认 `data/market_history/` 已生成，输出显示缓存可用和增量刷新状态。
4. 离线 / 缓存验证：
   - `python C:/Users/xiezhibo/.claude/skills/sp500-nasdaq100-gold-dca/scripts/dca_calculator.py --amount 5000 --no-refresh --base-dir C:/Users/xiezhibo/.claude/skills/sp500-nasdaq100-gold-dca`
5. 输出验收：
   - JSON 包含 `last_records`、`monthly_budget_status`、`markets`、`portfolio`、`wide_table`、`decision`、`recording_templates`。
   - 宽表包含组合汇总、三资产、现金 / 待投预算。
   - 无交易历史时不报错，XIRR 为 `null` / “暂无”。
   - 有交易历史时可显示每资产和组合估值、盈亏、收益率、XIRR。
   - 今日建议金额落在配置档位内，并受自然月剩余预算约束。
   - 输出标注行情来源、缓存状态和今日建议原因。

## Tool-call overflow prevention during implementation

- 不使用大型 `multi_tool_use.parallel`。
- 每轮通常只发起 1~5 个工具调用。
- 不重复 `Update Todos`，只维护一份 todo。
- 不把每个文件、每个检查项、每个行情点拆成一个 tool call。
- 大文件优先用一次 `Write` 或少量 `Edit` 修改。
- 批量计算和批量验证全部交给本地 Python 脚本一次运行。
- 只读取最终 JSON 摘要或关键验证输出。
- 如需额外探索，最多使用 1 个 Explore agent，不与大量 Read/Edit 混在同一消息中。

# Investment DCA Skill 全新改版计划

## Context

当前 `investment-dca` skill 已能抓取 Yahoo Finance 行情，为标普500、纳指100、黄金计算均线、RSI、252日分位、年化波动等信号，并按固定金额生成三资产分配建议；也能用 `--record` 写入本地模拟组合，展示累计持仓、收益率和 XIRR。

但现有体验仍偏向“每天固定 1500 元分配”：默认金额固定，总投入不会根据市场信号动态加减；输出混合了项目符号、段落和表格；组合复盘排在今日分配之后；也没有 2~3 天择日定投、5000 元基础金额、今日不买/延后观察、跳过买入观察日志等能力。

本次目标是把它升级成新的定投决策助手：**先复盘上一期记录、累计持仓、累计收益和 XIRR，再根据当前市场信号决定今天是否买、买多少、怎么分配**。所有数据、结果、解释和风险提示都以 Markdown 表格输出，提升可读性和执行纪律。

## Critical Files

| 文件 | 改动目的 |
|---|---|
| `C:/Users/xiezhibo/.claude/skills/investment-dca/scripts/dca_advisor.py` | 重构 CLI、账本兼容、买入决策、记录行为、JSON schema 和全表格 Markdown 输出 |
| `C:/Users/xiezhibo/.claude/skills/investment-dca/SKILL.md` | 更新 skill 描述、默认调用方式、参数说明、输出要求和风险边界 |
| `C:/Users/xiezhibo/.claude/investment-dca/portfolio.json` | 运行时兼容维护；新增 `schema_version`、`settings`、`observations`，不破坏旧记录 |
| `C:/Users/xiezhibo/.claude/investment-dca/daily_records.csv` | 继续保存模拟买入记录；本次不强制迁移旧 CSV 表头 |

真实交易和模拟交易分离本次只预留字段，不做完整拆账，避免一次性迁移过大。

## Existing Code To Reuse

| 现有函数/结构 | 文件 | 复用方式 |
|---|---|---|
| `ASSETS` | `scripts/dca_advisor.py` | 保留三资产配置、基础权重、上下限、名称和代码 |
| `fetch_chart()` / `fetch_quote()` / `fetch_usdcny()` | `scripts/dca_advisor.py` | 继续作为 Yahoo Finance 行情与 USD/CNY 汇率来源 |
| `Quote` / `Metrics` | `scripts/dca_advisor.py` | 继续作为行情和指标数据结构 |
| `compute_metrics()` | `scripts/dca_advisor.py` | 继续计算 MA20/60/120/200、RSI14、252日分位、距高点、波动、信号标签和原因 |
| `recommend_weights()` / `normalize_weights()` | `scripts/dca_advisor.py` | 继续负责三类资产间动态权重；外层新增“今日总金额决策” |
| `load_ledger()` / `save_ledger()` / `append_csv()` | `scripts/dca_advisor.py` | 扩展账本 schema，但保持旧记录可读 |
| `portfolio_value()` / `build_position_rows()` | `scripts/dca_advisor.py` | 继续用于组合市值、成本、盈亏和累计持仓表 |
| `annualized_return()` / `xirr()` / `portfolio_xirr()` | `scripts/dca_advisor.py` | 继续用于年化收益和组合 XIRR |
| `fmt_money()` / `fmt_pct()` / `fmt_num()` / `fmt_units()` | `scripts/dca_advisor.py` | 继续用于表格格式化 |

## Recommended Implementation

### 1. 更新 CLI 语义

| 参数 | 新行为 |
|---|---|
| `[amount]` | 保留为用户手动覆盖今日金额；默认从 `1500.0` 改为 `None` |
| `--base-amount` | 新增，默认 `5000.0` |
| `--monthly` | 保留，默认 `30000.0` |
| `--record` | 保留；仅当今日决策为买入，或用户使用 `--force-buy` 时写入模拟买入 |
| `--record-skip` | 新增；记录今日延后/跳过观察，不改变持仓和 XIRR 现金流 |
| `--force-buy` | 新增；当脚本建议延后但用户明确要买时，允许记录模拟买入 |
| `--json` | 保留并扩展输出 schema |
| `--section5` | 保留兼容，输出新版组合相关表格或兼容提示 |
| `--reset` | 保留；继续删除本地模拟记录 |

默认调用从固定 `1500 --monthly 30000` 改为：

```bash
python /c/Users/xiezhibo/.claude/skills/investment-dca/scripts/dca_advisor.py --base-amount 5000 --monthly 30000
```

### 2. 兼容式升级本地账本

新增 `ensure_ledger_schema(ledger)`，加载旧账本时自动补齐但不破坏旧字段。

| 字段 | 行为 |
|---|---|
| `schema_version` | 旧账本没有时视为 v1；新保存写 v2 |
| `settings` | 补齐 `base_amount_cny: 5000.0`、`cadence_days_min: 2`、`cadence_days_max: 3` |
| `observations` | 新增数组，保存跳过/延后观察 |
| `positions` / `records` | 保持旧结构兼容，读取旧记录时全部用 `.get()` |

新买入记录增加字段：

| 字段 | 用途 |
|---|---|
| `record_type: simulated_buy` | 标记当前仍是模拟买入 |
| `account_type: simulated` | 为未来真实/模拟分账预留 |
| `decision_action` | 记录当日决策来源 |
| `base_amount_cny` | 当日基准金额 |
| `recommended_amount_cny` | 当日建议总金额 |
| `amount_adjustment_json` | 金额调整因子快照 |
| `signals_snapshot_json` | 信号快照，便于复盘 |

新增 `record_observation(...)` 写入 `ledger["observations"]`，不改变 `positions`，不进入 `records`，不参与 `portfolio_xirr()`。

### 3. 拆分业务逻辑

把当前 `main()` 中的抓取、计算、记录、输出拆成可复用函数。

| 新函数 | 作用 |
|---|---|
| `ensure_ledger_schema(ledger)` | 兼容旧账本并补齐 v2 字段 |
| `get_last_record_summary(ledger, today)` | 最近买入记录、距今天数、今日是否已记录 |
| `compute_portfolio_snapshot(ledger, quotes, usdcny, today)` | 组合总览、持仓明细、XIRR |
| `weighted_market_signal(metrics, weights=None)` | 计算组合层面的市场冷热信号 |
| `decide_dca_action(...)` | 决定 `buy` / `defer` / `skip`、建议金额、原因 |
| `build_amount_adjustments(...)` | 生成金额动态加减的表格行 |
| `build_allocations(amount, weights)` | 抽出现有金额分配与 rounding drift 修正 |
| `record_buy(...)` | 抽出现有 `--record` 写入逻辑 |
| `record_observation(...)` | 记录跳过/延后观察 |
| `markdown_table(headers, rows)` | 统一 Markdown 表格输出 |
| `build_result(...)` | 统一生成 JSON 与 Markdown 共用结果对象 |
| `print_markdown_tables(result)` | 输出全表格 Markdown |

### 4. 今日是否买入与金额算法

新增总金额决策层，不替代现有资产权重算法。

| 条件 | 默认行为 |
|---|---|
| 今天已有买入记录 | `skip`，建议金额 0，避免重复 |
| 距上次买入 0 天 | `skip` |
| 距上次买入 1 天且无明显低位/大跌 | `defer`，建议金额 0 |
| 距上次买入 2~3 天 | `buy`，基准约 5000 |
| 距上次买入 ≥4 天 | `buy`，可小幅补节奏 |
| 市场综合信号明显低位 | 在 5000 基础上加 20%~50% |
| 单日大跌且预算允许 | 加 10%~30% |
| 市场偏热 | 减 30%~70% 或延后 |
| 三类资产整体偏热 | 默认 `defer` 或仅保留小额观察建议 |
| 用户传入 `[amount]` | 作为手动覆盖金额，并在表格中说明“用户覆盖” |

每个金额调整项都输出为一行；最终金额四舍五入到易读粒度（如 100 元）。若决策为 `defer/skip`，实际分配金额为 0；可另展示“如果强制买入的参考分配”，但必须明确不是今日建议买入。

### 5. 全表格 Markdown 输出

非 JSON 输出中可以保留标题，但所有数据、解释、风险、下一步都用表格，不再使用项目符号和散文段落。

默认输出顺序：

| 顺序 | 表格 |
|---:|---|
| 1 | 上次记录 / 复盘表：最近日期、距今天数、最近投入、最近收益、最近 XIRR、今日是否已记录 |
| 2 | 组合总览表：累计期数、成本、市值、盈亏、收益率、年化、XIRR |
| 3 | 累计持仓表：资产、代码、份额、成本、市值、盈亏、收益率、仓位 |
| 4 | 今日买入决策表：买/延后/跳过、建议金额、节奏判断、市场判断、理由 |
| 5 | 金额调整因子表：基准、时间节奏、市场低位/高位、预算约束、最终金额 |
| 6 | 今日分配表：资产、代码、建议金额、权重、信号标签、执行说明 |
| 7 | 行情指标表：价格、日涨跌、MA20/60/120/200、RSI、252日分位、距高点、波动 |
| 8 | 信号解释表：信号分数、信号标签、原因、基础权重、调整后权重 |
| 9 | 写入状态 / 下一步表：是否写入、可用命令、记录类型 |
| 10 | 风险提示表：投资属性、数据延迟、实盘差异、汇率、模拟记录 |

### 6. 扩展 JSON 输出

`--json` 保留旧字段并新增：

| 字段 | 内容 |
|---|---|
| `base_amount_cny` | 基准金额 |
| `recommended_amount_cny` | 今日建议总金额 |
| `decision` | 今日动作、标签、原因、距上次记录天数 |
| `last_record` | 最近记录摘要 |
| `positions` | 持仓明细数组 |
| `amount_adjustments` | 金额调整因子数组 |
| `signals` | 信号解释数组 |
| `recording` | 写入请求、是否写入、记录类型、提示 |
| `warnings` | 风险提示数组 |

### 7. 更新 `SKILL.md`

| 区域 | 更新内容 |
|---|---|
| 默认行为 | `/investment-dca` 使用 5000 基准、月度 30000，并自动判断是否买入 |
| 核心流程 | 先复盘上一条记录和累计收益，再给今日决策 |
| 输出要求 | 所有数据、结果、解释、风险提示均用表格输出 |
| CLI 示例 | 加入 `--base-amount`、`--record-skip`、`--force-buy` |
| 记录说明 | `--record` 当前写入模拟组合，真实交易记录后续单独扩展 |
| 风险边界 | 保留“不保证收益、不构成个性化投资建议、不鼓励高杠杆” |

## Verification Plan

### 静态验证

| 验证项 | 命令 |
|---|---|
| Python 语法 | `python -m py_compile C:/Users/xiezhibo/.claude/skills/investment-dca/scripts/dca_advisor.py` |
| 参数帮助 | `python C:/Users/xiezhibo/.claude/skills/investment-dca/scripts/dca_advisor.py --help` |
| JSON 合法性 | `python C:/Users/xiezhibo/.claude/skills/investment-dca/scripts/dca_advisor.py --json` |

### 功能验证

| 场景 | 期望 |
|---|---|
| 无账本默认运行 | 显示无最近记录、组合为 0、XIRR 为 N/A、默认基准 5000 |
| 默认运行不带 `--record` | 不写入账本，只输出建议 |
| `--record` 且决策为 buy | 写入模拟买入，更新持仓、成本、市值和记录 |
| 同日再次 `--record` | 不重复买入，写入状态表提示已记录 |
| `--record-skip` | 写入 `observations`，不改变持仓，不影响 XIRR |
| `--force-buy --record` | 在延后场景也能按用户要求记录模拟买入 |
| 旧账本读取 | 无 `schema_version`、旧记录缺少新字段时不报错 |
| `--section5` | 不崩溃，能输出新版组合相关表格或兼容说明 |

### 节奏与金额验证

| 上次记录距今天数 | 市场中性时期望 |
|---:|---|
| 0 | skip |
| 1 | defer |
| 2 | buy，约 5000 |
| 3 | buy，约 5000 |
| 4+ | buy，可小幅补节奏 |

| 市场场景 | 期望 |
|---|---|
| 明显低位/大跌 | 建议金额高于 5000 |
| 明显高位偏热 | 建议金额低于 5000 或 defer |
| 用户传入金额 | 表格注明用户覆盖，按用户金额决策/分配 |
| 月度预算不足 | 限制金额或表格提示预算约束 |

### 输出验证

| 检查点 | 期望 |
|---|---|
| 默认 Markdown | 除标题外，数据、解释、风险和下一步均以表格输出 |
| 表格可读性 | 拆成多张窄表，避免单张超宽表难读 |
| 行情失败 | 用表格或 stderr 清楚说明失败原因，不写入记录 |
| CNY=X 失败 | 使用 7.20 fallback，并在运行/风险表中说明 |

## Implementation Notes

| 注意点 | 说明 |
|---|---|
| 观察记录不进 XIRR | `observations` 只做行为复盘，不作为现金流 |
| 旧记录兼容 | 新字段都用 `.get()` 读取，不能要求旧记录存在 |
| no-buy 不分配非零金额 | `defer/skip` 时实际 `allocations_cny` 应为 0 |
| 可显示参考分配 | 延后时可显示强制买入参考分配，但必须清楚标注不是今日建议 |
| XIRR 不足显示 N/A | 同日或现金流不足时不要强行显示 0 |
| 输出不要混用散文 | 风险提示、下一步、原因都放表格 |
| `--record` 遇到 defer | 默认不写买入；用户需要 `--force-buy --record` 才强制记录 |

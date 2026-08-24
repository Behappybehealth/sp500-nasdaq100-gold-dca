# 架构 Code Review（2026-08-24）

> 范围：全项目 7283 行 Python 的架构级审查，聚焦核心代码、架构设计、数据流、耦合度、问题与优化点。

## 一、架构总评

设计水准明显高于平均个人项目。核心架构决策——**引擎子进程隔离**（Web 和 Claude Skill 共用同一计算引擎，进程崩溃不拖垮 Web）——是正确的。

**耦合度总评：中上**。大部分模块边界清晰，但存在 3 处"为捷径牺牲了分层"的耦合点，和 2 处文档已知的重复实现。

## 二、依赖关系图（实际 import 链）

```
app.py (装配, 70行)
 ├→ storage          ──→ streamlit/pandas/gspread  (⚠ 数据层反向依赖 UI 框架)
 ├→ src/context       ──→ (纯 dataclass, 无依赖)  ✅
 ├→ src/obs           ──→ (纯 logging, 无依赖)    ✅
 ├→ src/ui/auth       ──→ storage, ..state, .overlays
 ├→ src/ui/sidebar    ──→ storage, ..services.model, ..services.quotes, ..state  (协调器)
 ├→ src/ui/styles     ──→ (纯 CSS)  ✅
 ├→ src/tabs/today    ──→ ..services.model (parse_wide_table)  ⚠ 往返
 ├→ src/tabs/records  ──→ storage, ..state
 ├→ src/tabs/holdings ──→ ..services.curves
 ├→ src/tabs/backtest ──→ ..services.curves (_load_json)
 ├→ src/services/model ──→ subprocess → scripts/dca_calculator  ✅ 隔离
 ├→ src/services/quotes──→ ..context (⚠ @st.cache_data 绑定 Streamlit)
 └→ src/services/curves──→ sys.path.insert + import dca_calculator  ⚠ 动态导入

scripts/dca_calculator.py (1391行引擎) ──→ stdlib only  ✅ 零反向依赖
scripts/dca_action.py     ──→ dca_calculator, storage  (CLI 入口)
```

**关键边界**：引擎（`dca_calculator.py`）零依赖 `src/` 和 `storage`——子进程隔离能成立的前提。`storage.py` 不反向 import `src/`——数据层干净。这两条底线守住了。

## 三、问题与优化点（按优先级）

### 🔴 P1-1：`render_wide_table` ↔ `parse_wide_table` 数据往返

**现状**：引擎 `render_wide_table()`（95 行）把结构化 `result` dict 渲染成 19 列中文 markdown 表 → 塞进 `result["wide_table_markdown"]` → Web 端 `today.py:61` 调 `parse_wide_table()`（4 行脆弱 `split("|")` 解析器）把 markdown **解析回** DataFrame。

**问题**：
1. 数据→markdown→数据往返，丢失全部类型信息（数字变字符串）
2. 引擎承担展示职责（19 列表头、中文标签硬编码在计算引擎里）
3. `parse_wide_table` 极脆弱——markdown 格式一动就崩，无转义处理
4. 同文件自相矛盾：`today.py` 下方"今日行情与评分"表已直接从结构化数据建 DataFrame，"累计持仓"表却走往返

**修改方案**：提取结构化中间层 `build_wide_rows() -> list[dict]`，markdown（给 Skill）和 DataFrame（给 Web）各取所需。引擎 `main()` 多输出 `result["wide_table_rows"]`，`today.py` 直接用，删 `parse_wide_table`。

### 🔴 P1-2：交易行 schema 三处重复定义

**现状**：`transactions` 行字段在 `storage.py`(`TX_FIELDS`)、`dca_action.py`(内联)、`records.py`(内联) 各写一遍。`observations` 同理。

**问题**：加/改/删字段要改 3 处，漏一处就字段错位（CSV 按位置写，错位=数据污染）。

**修改方案**：`storage.py` 加 `build_tx_row()` / `build_obs_row()` 构造函数，三处统一调用。

### 🟡 P2-1：`curves.py` 动态 `sys.path.insert` 导入引擎

**现状**：`load_price_series()` 运行时 `sys.path.insert` + `import dca_calculator`，只为用 3 个函数。`portfolio_curve()` 还重复实现了引擎的持仓累积和 `close_at_or_before`。

**修改方案**：把缓存读写纯函数抽成独立模块 `market_cache.py`，引擎和 curves 共用。

### 🟡 P2-2：`storage.py` 反向依赖 Streamlit（不改）

`dca_action.py` 用 `os.chdir` hack 能工作，短期不加非 Streamlit 入口则不改。

### 🟡 P2-3：`quotes.py` 两种 HTTP 机制 + BTC 无兜底

**现状**：`fetch_xau_spot` 用 curl + 有落盘兜底；`fetch_btc` 用 urllib + 无兜底（失败直接返回 None）。

**修改方案**：给 `fetch_btc` 加落盘兜底，失败时读旧值并标注"更新失败，使用历史数据"。

### 🟢 P3：可接受的权衡（不改）

| 项 | 为什么可接受 |
|---|---|
| `biz_today()` 双实现 | 文档已标注"两处必须同改"；引擎 import src/ 会破坏隔离 |
| sidebar 双跑模型 | 快照命中时几乎即时；UX 需要先展示行情再输入金额 |
| `main()` 180 行 | 各 helper 已拆分，main 是线性编排 |
| auth/sidebar 内联 HTML | Streamlit 本质限制，CSS 已集中在 styles.py |

## 四、做得好的地方（不要动）

1. **子进程隔离**：`run_model()` subprocess 调引擎，user 进缓存键防串号，stderr 留痕，degraded 记 warning
2. **storage.py 安全设计**：SheetRead/WriteError 严格区分、写前快照 fail-closed、PBKDF2+盐+rehash-on-login、锁定机制
3. **auth.py 三阶段状态机**：fail-closed、PIN 不入日志、点击趟零网络
4. **conftest.py 拒网守卫**：堵死全部 4 条出网路径
5. **state.py 键登记表**：session_state 键集中常量化
6. **引擎评分模型**：趋势/动量只防御不追高、回撤受趋势门控、热度过热二次项、权重 λ 二分求解、market_freshness 闸
7. **显式收参**：所有 tab render 显式收参，不读模块级全局

## 五、修改优先级

| 优先级 | 项 | 改动量 | 收益 |
|---|---|---|---|
| 先做 | P1-1 往返消除 | 引擎 1 函数 + today.py 1 行 | 删脆弱解析器、恢复类型 |
| 先做 | P1-2 schema DRY | storage +2 函数，改 2 处 | 消除字段错位风险 |
| 值得做 | P2-1 curves 动态导入 | 抽 4 函数成模块 | 静态分析恢复 |
| 顺手 | P2-3 quotes 兜底 | 小 | BTC 失败有兜底+标注 |

## 六、引擎 1400 行功能切分建议

`scripts/dca_calculator.py` 1391 行、56 个函数全在一个文件，职责混杂：日期工具、数据读取、网络抓取、缓存、汇率、组合数学、评分、决策、表格渲染、main 编排。建议按职责切分为多模块（详见后续优化方案）。

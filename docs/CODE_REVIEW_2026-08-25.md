# 全新视角专业代码复审（2026-08-25）

> 【冻结产物 · 评审日快照】第二轮全量复审。第一轮（`CODE_REVIEW_2026-08-24.md`）识别的 P1-1/P1-2/P2-1/P2-3 已全部落地、引擎已拆分为 6 模块后，以"首次接触本项目的资深研发专家"视角逐文件通读全部源码（~7,500 行 / ~40 个 .py 文件）+ 全部测试（2,080 行 / 130 用例）+ 全部配置与文档后给出。
> 评审 HEAD：`f0bce5d` · 评审日期：2026-08-25
> 本文件是评审日快照，不随代码改动而更新；后续 CR 另建新文件，旧文件留存供对比。

---

## 一、总体印象（Executive Summary）

这是一个**完成度远超个人项目水准**的定投决策系统。如果按"个人/小团队项目"的标尺衡量，它处于**前 5%** 的梯队；如果按"商业级生产系统"的标尺衡量，它**及格偏上**——主要短板不在代码本身，而在架构选型带来的固有约束。

**一句话评价**：代码写得像一群有纪律的工程师在改，而不是一个人在堆——这在个人项目里极其罕见。

**三个最突出的优点**：
1. **子进程边界设计**（`app.py` ↔ `dca_calculator.py`）是教科书级的关注点分离——计算与 UI 零共享内存，改一侧不影响另一侧
2. **防御性编程密度极高**——35 个 bug 全部带"四段工作流"（梳理→确认→修复→验证），每个 `except` 都带 `as e` 进日志，读失败与空表严格区分，新鲜度闸防旧价冒充实时价
3. **测试工程化程度惊人**——130 个离线测试，autouse 拒网守卫自测、AST 契约断言（引擎改输出形状测试立刻红）、PIN 安全 AST 扫描

**三个最突出的短板**：
1. **storage.py 反向依赖 UI 框架**（`import streamlit as st`）——数据层不该认识 UI 框架，这是分层违规
2. **Google Sheets 当 OLTP**——整表读/整表写 + 8 秒进程内缓存，并发写有竞态，已判定不修（⚪ BUG-019）但它是结构性天花板
3. **双实现同规则**（`biz_today()` 在 `src/dates.py` 和 `scripts/dca_types.py` 各一份）——子进程隔离的代价，但"两处必须同改"是个定时炸弹

---

## 二、架构评估

### 2.1 架构全景

```
app.py (70行纯装配)
 ├→ storage.py (666行, 数据层)        ⚠ 反向依赖 streamlit
 ├→ src/ (22文件, 2164行, 业务层)
 │   ├── context.py (路径装配, 纯dataclass)
 │   ├── obs.py (日志配置, 纯logging)
 │   ├── state.py (session_state键登记表)
 │   ├── market_cache.py (行情缓存读, 纯函数)
 │   ├── services/ (model=子进程调引擎, quotes=行情, curves=曲线)
 │   ├── ui/ (auth=门闸, sidebar=模型执行点, styles=CSS, overlays=遮罩)
 │   └── tabs/ (6个tab: today/holdings/records/history/backtest/strategy_doc)
 └→ scripts/ (8文件, 1811行, 计算引擎)
     ├── dca_calculator.py (240行入口+re-export)
     ├── dca_types → dca_market → dca_portfolio → dca_scoring → dca_table (5兄弟, 1506行)
     ├── dca_action.py (CLI薄壳)
     └── changelog.py (工具)
```

**架构评分：8/10**

**亮点**：
- ✅ **三层 + 一个边界**清晰：装配层 → 业务层 → 数据层，子进程边界隔离计算
- ✅ **引擎拆分为线性 DAG**（`dca_types → dca_market → dca_portfolio → dca_scoring → dca_table`），无循环引用，re-export 保调用方零改动
- ✅ **显式收参**：所有子模块收 `paths: Paths` / `user: str`，不读 `app.py` 模块级全局
- ✅ **双入口共用业务层**：Web（`app.py`）与 Skill（`dca_action.py`）都走 `storage.py`

**问题**：
- ⚠ **storage.py 认识 streamlit**（L56 `import streamlit as st`）——数据层反向依赖 UI 框架。`@st.cache_resource` 绑定连接、`st.secrets` 读凭据，导致 storage 无法脱离 Streamlit 独立测试或复用
- ⚠ **`src/services/quotes.py` 的 `@st.cache_data` 绑定 Streamlit**——行情抓取函数与 UI 框架耦合，无法在非 Streamlit 环境复用
- ⚠ **`biz_today()` 双实现**——`src/dates.py` 和 `scripts/dca_types.py` 各一份同规则代码，注释说"两处必须同改"，但没有任何机制保证同步（没有测试断言两处实现一致）

### 2.2 数据流评估

**数据流评分：7/10**

一次"打开页面看今日建议"的完整链路：
```
用户登录 → storage.authenticate() (Sheets users表)
         → storage.sync_local() (云端→本地缓存)
         → sidebar.render() → run_model() (subprocess起引擎)
           → 引擎读 config.json + transactions.csv + market_history/*.csv
           → 引擎抓 Yahoo Chart v8 (8请求并发) + 东财XAU + Yahoo BTC
           → 引擎算评分→部署系数→权重→金额
           → 引擎输出 JSON 到 stdout
         → 侧栏解析 JSON → 6个tab渲染
```

**亮点**：
- ✅ 行情快照 TTL 600s，连续重跑近即时
- ✅ 8 个外部请求并发同波，总耗时取最大值（实测 1.5s）
- ✅ 新鲜度闸：拿不到实时价就不出金额（旧价冒充实时价是最危险的静默失败）

**问题**：
- ⚠ **整页重算模型**：Streamlit 每次交互重跑整个脚本，`run_model` 靠 `@st.cache_data(ttl=900)` 兜底，但用户改金额输入会触发**第二次完整模型重算**（虽然快照命中时近即时，但设计上仍是"全量重跑"而非"增量更新"）
- ⚠ **storage 的 8 秒进程内缓存**（`_SHEET_CACHE_TTL = 8.0`）——多用户场景下 8 秒内的一致性窗口，单管理员团队可接受，但这是个隐式约定

---

## 三、代码质量评估（按层）

### 3.1 计算引擎层（scripts/）— **9/10**

这是全项目**质量最高**的一层。

**亮点**：
- ✅ **纯 stdlib**（除 yfinance 兜底），零反向依赖，可独立测试
- ✅ **落库三道护栏**（`save_cached_closes`）：剔盘中价 + 行数不减拒写 + 原子替换（temp + os.replace）
- ✅ **冷热分离**：已收盘定稿值归 csv，当日未收盘值归 `market_live.json`，合并时 csv 优先
- ✅ **每次回退 5 天重抓**（`_REFETCH_LOOKBACK_DAYS`）：数据源事后回填 null 空洞、修正错值都会自动追平
- ✅ **XIRR 实现严谨**：Newton-Raphson + 二分法兜底，处理无解情况，期短不年化
- ✅ **权重归一化**（`score_based_weights`）：解 λ 使 `Σ clamp(λ·w, min, max) = 1`，构造性保证和为 1 且不越界

**小问题**：
- `dca_market.py` 的 `fetch_json` 重试退避固定（0.8s / 1.6s），非指数退避——但 3 次足够，影响不大
- `dca_scoring.py` 的 `asset_score` 函数 60 行、7 个分量，可读性尚可但缺少中间值注释（哪些是 raw、哪些是 clipped）
- `dca_calculator.py` 的 `main()` 仍有 ~180 行，虽然已从 1424 行拆出，但 main 本身可进一步拆分（行情段 / 预算段 / 决策段 / 输出段）

### 3.2 业务层（src/）— **8/10**

**亮点**：
- ✅ **`src/state.py` 是全项目最精巧的设计**——session_state 键登记表，11 个键各标归属链与生命周期，`ALL_KEYS` frozenset 被 `test_state.py` 断言与实际用键互相覆盖
- ✅ **`src/obs.py` 日志设计**：`dca.*` 子树不碰 root logger，幂等配置，stderr + 轮转文件双落点
- ✅ **`src/ui/auth.py` 三阶段状态机**（login/activate/bootstrap）+ 两段式防残留（点击趟零网络 → ph.empty() 真删除 → 遮罩 → rerun）
- ✅ **`src/market_cache.py` 纯函数**——从引擎抽出的读缓存函数，无 Streamlit 依赖，消除了 `curves.py` 的 `sys.path` hack

**问题**：
- ⚠ **`src/ui/sidebar.py` 339 行**，是全项目最大的 UI 文件——行情展示 + 预算表单 + 汇率展示 + 用户管理 + 本地迁移全塞在一个 `render()` 里，职责过重
- ⚠ **`src/ui/auth.py` 365 行**，`require_user()` 单函数 ~120 行，嵌套 3 层 if-elif（login/activate/bootstrap），可读性压力大
- ⚠ **`src/tabs/backtest.py` 248 行**，5 段静态报告全硬编码在 Python 字符串里——虽然数据出库了（BUG-025），但文案仍是代码，改文案要改代码
- ⚠ **`src/services/model.py` 的 `run_model` 用 subprocess 起引擎**——`subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=180)`，180s 超时在 Yahoo 限流时可能不够（虽然并发后实测 1.5s）

### 3.3 数据层（storage.py）— **7/10**

**亮点**：
- ✅ **PBKDF2 + 随机盐**（20 万迭代），旧 SHA256 账号登录时自动迁移
- ✅ **fail-closed**：secrets 缺失/损坏即拒启动，仅显式 `DCA_AUTH_MODE=local` 进单机
- ✅ **读失败与空表严格区分**（`SheetReadError` vs 空 DataFrame）——"我不知道"绝不伪装成"没有"
- ✅ **写前快照**（`_write_ws` 写前先备份到 `<name>_bak`，快照失败则放弃写入）
- ✅ **`build_tx_row` / `build_obs_row`** 统一构造行字典，Web 与 CLI 共用，消除三处各写一遍字段

**问题**：
- ⚠ **`import streamlit as st`**（L56）——数据层反向依赖 UI 框架，这是全项目最明显的分层违规。`@st.cache_resource` 绑定连接、`st.secrets` 读凭据，导致 storage 无法脱离 Streamlit 独立测试（测试里要 `monkeypatch` `sheets_enabled`）
- ⚠ **整表读/整表写**——`_read_ws` 读整个 worksheet，`_write_ws` 整表覆写。小团队可接受，但这是结构性天花板（BUG-019 判定不修 ⚪）
- ⚠ **8 秒进程内缓存**（`_SHEET_CACHE`）——写后即时失效，但多用户并发写有理论竞态
- ⚠ **`_conn()` 的猴子补丁**（给 `streamlit_gsheets.gsheets_connection.cache_data` 打 `_quiet_cache_data` 补丁）——补丁第三方库内部行为，上游升级可能失效

### 3.4 测试层（tests/）— **9.5/10**

这是全项目**最让人印象深刻**的一层。

**亮点**：
- ✅ **130 个测试全离线**——autouse `_deny_network` 拦四口（socket.connect / connect_ex / getaddrinfo / create_connection / subprocess.Popen），回环放行
- ✅ **拒网守卫自测**（`test_offline_guard.py`）——守卫本身也被罩住，守卫失效这个文件先红
- ✅ **AST 契约断言**（`test_smoke.py::_engine_result_keys`）——从引擎源码 AST 抠出 `main()` 的输出键集，虚构 fixture 的键集必须一致，引擎改输出形状立刻红
- ✅ **PIN 安全 AST 扫描**（`test_obs.py`）——日志调用点绝不引用 PIN/哈希/盐，用 AST 扫全部调用点
- ✅ **session_state 登记表断言**（`test_state.py`）——登记表与实际用键互相覆盖，既不许未登记键也不许死常量
- ✅ **弃用 API 零残留**（`test_deprecated_api.py`）——`use_container_width` 零残留 + 下界托底
- ✅ **备份脚本不变量**（`test_backup_script.py`）——Code.gs 的 TABLES 与 storage.py 工作表名脱钩检测

**小问题**：
- `test_smoke.py` 的 `build_result` 函数 ~80 行，手组装引擎输出——虽然注释解释了为什么不用 fixture（私人财务数据 + 形状漂移），但维护成本高
- 没有**端到端集成测试**（从 CLI 输入到引擎输出到 storage 写入的完整链路）——不过冒烟测试覆盖了大部分

---

## 四、工程实践评估

### 4.1 CI/CD — **8/10**

- ✅ push/PR 全分支触发
- ✅ 三道门禁：ruff → mypy → pytest
- ✅ Win/3.14 + Linux/3.12 双腿，fail-fast: false
- ✅ junit XML 汇总到 Summary + artifact 留档 90 天
- ✅ 不依赖 secrets（测试全离线）

**小问题**：没有**定时跑**（cron）——Yahoo 限流是间歇性的，定时跑能发现"今天挂了"的回归

### 4.2 依赖管理 — **8/10**

- ✅ 两份分工：`requirements.txt`（Cloud 可安装范围，有上界）+ `requirements-dev.lock`（开发机全量精确锁定）
- ✅ 上界拦住上游大版本改动（pandas 2→3 不兼容）
- ✅ streamlit 下界钉 1.61.1（`width="stretch"` 参数 1.46+ 才有）

### 4.3 文档 — **9/10**

- ✅ **STRUCTURE.md**：依赖关系图 + 架构一览 + 英文/中文文件树，唯一事实源
- ✅ **ARCHITECTURE.md**：顶层架构唯一事实源，活文档标记更新时机
- ✅ **ARCHITECTURE-DETAIL.md**：实现细节与踩坑记录
- ✅ **BUGLIST.md**：35 个 bug 全带四段工作流，问题唯一事实源
- ✅ **CLAUDE.md**：14 条编码规则 + 数据口径，给 AI 助手的上下文
- ✅ **CHANGELOG.md**：`changelog.py add <hash>` 生成 + `--check` 校验覆盖

### 4.4 可观测性 — **7/10**

- ✅ `dca.*` 日志子树，stderr + 轮转文件双落点
- ✅ 16 个埋点（auth 9 / storage 4 / model 3），只记失败与降级
- ✅ 绝不记 PIN/哈希/盐（AST 扫描断言）
- ⚠ **零外部探针**（BUG-034 判定不修 ⚪）——HTTP 200 探针对"活着但坏了"失明，但用户确认只有一人使用，可接受
- ⚠ **零告警**——日志要人去看，没有主动通知

### 4.5 备份与恢复 — **8/10**

- ✅ Apps Script 每日快照四表到 Drive，30 天保留
- ✅ 失败邮件告警
- ✅ 恢复拒源表守卫
- ✅ 演练已通过（BUG-018 🟠→✅，2026-08-24 真跑恢复验证）
- ✅ 全在 Google 侧执行，不依赖公司电脑开机

---

## 五、全新视角发现的问题

以下是我**首次通读**发现的问题，按严重度排序。注意：已知的 35 个 bug 不在此列（那些已被识别并处理）。

### 🔴 P0（数据会丢/会串号）

**无。** 全项目 P0 级问题已清零（5/5 修复）。

### 🟡 P1（会给错误钱数或关键时刻失败）

**P1-NEW-1：`biz_today()` 双实现无同步保证**
- `src/dates.py` 和 `scripts/dca_types.py` 各一份同规则实现，注释说"两处必须同改"
- 但**没有任何测试断言两处实现一致**——如果有人改了一处忘了另一处，测试全绿但线上日期错位
- **建议**：加一条测试，用固定时刻断言两处 `biz_today()` 返回一致

**P1-NEW-2：`storage.py` 的 `_conn()` 猴子补丁第三方库**
- L180-190 给 `streamlit_gsheets.gsheets_connection.cache_data` 打 `_quiet_cache_data` 补丁
- 上游升级改了内部结构，补丁静默失效，`show_spinner` 又开始闪前端
- **建议**：补丁加版本断言（`assert _gc.__version__ == "x.y.z"`），或上游 PR 关掉 show_spinner

### 🟠 P2（长期负债）

**P2-NEW-1：`sidebar.py` 339 行职责过重**
- 行情展示 + 预算表单 + 汇率展示 + 用户管理 + 本地迁移全在一个 `render()` 里
- **建议**：拆为 `_render_quotes()` / `_render_budget()` / `_render_admin()` / `_render_migration()` 四个私有函数

**P2-NEW-2：`auth.py` 的 `require_user()` 120 行嵌套 3 层**
- login/activate/bootstrap 三阶段 if-elif 嵌套，可读性压力大
- **建议**：每阶段抽成 `_handle_login_stage()` / `_handle_activate_stage()` / `_handle_bootstrap_stage()`

**P2-NEW-3：`backtest.py` 248 行硬编码文案**
- 5 段报告的文案（"为什么定额等比收益反而最高"等）是 Python 字符串
- 改文案要改代码、过 CI、重新部署
- **建议**：文案移到 `backtest/` 下的 markdown 文件，`backtest.py` 只负责读数据 + 渲染

**P2-NEW-4：`dca_calculator.py` 的 `main()` 仍 180 行**
- 虽然已从 1424 行拆出，但 main 本身可进一步拆分（行情段 / 预算段 / 决策段 / 输出段）
- **建议**：抽 `_fetch_market_data()` / `_compute_budget()` / `_build_result()` 三个私有函数

### 🟢 P3（可接受的技术债）

**P3-NEW-1：`storage.py` 反向依赖 streamlit**
- 数据层 `import streamlit as st`，无法脱离 Streamlit 独立测试
- 这是历史选型代价（`@st.cache_resource` 绑定连接），改它要换连接管理方式
- **已知**，P2-2 在上一轮评审已识别，用户判定不修（当前 chdir hack 可用）

**P3-NEW-2：`quotes.py` 的 `@st.cache_data` 绑定 Streamlit**
- 行情抓取函数与 UI 框架耦合
- **已知**，与 P3-NEW-1 同源

**P3-NEW-3：`dca_action.py` 的 `os.chdir(base_dir)` hack**
- L150 `os.chdir(base_dir)` 让 `st.secrets` 找到 secrets.toml——改进程全局状态
- **已知**，注释解释了原因，可接受

---

## 六、开发者评分

| 维度 | 分数 | 说明 |
|---|---|---|
| 架构设计 | 8/10 | 三层+边界清晰，子进程隔离是亮点；storage 反向依赖是污点 |
| 代码质量 | 8/10 | 防御性编程密度高，注释解释"为什么"不只"是什么"；部分大函数可拆 |
| 测试工程 | 9.5/10 | 130 离线测试，AST 契约断言、拒网守卫自测、PIN 安全扫描——教科书级 |
| 工程实践 | 8/10 | CI 三道门禁、双依赖清单、CHANGELOG 校验、BUGLIST 四段工作流 |
| 可观测性 | 7/10 | 日志设计好但零告警零探针（已判定不修） |
| 文档 | 9/10 | STRUCTURE/ARCHITECTURE/BUGLIST/CLAUDE 四份文档分工清晰 |
| 安全意识 | 8.5/10 | PBKDF2+盐、fail-closed、读失败拒写、绝不记 PIN |
| **综合** | **8.3/10** | **"above average"——个人项目前 5%梯队，商业级及格偏上** |

---

## 七、优化建议（优先级排序）

| 优先级 | 建议 | 工作量 | 收益 |
|---|---|---|---|
| **P1** | 加 `biz_today()` 双实现一致性测试 | 15min | 消除定时炸弹 |
| **P1** | `_conn()` 猴子补丁加版本断言 | 10min | 防上游升级静默失效 |
| **P2** | 拆 `sidebar.py` 的 `render()` 为 4 个私有函数 | 1hr | 可读性 |
| **P2** | 拆 `auth.py` 的 `require_user()` 为 3 个阶段函数 | 1hr | 可读性 |
| **P2** | `backtest.py` 文案移到 markdown 文件 | 30min | 文案与代码解耦 |
| **P2** | 拆 `dca_calculator.py` 的 `main()` 为 3 段 | 45min | 可读性 |
| **P3** | storage.py 去 streamlit 依赖（换连接管理） | 4hr+ | 分层纯净（用户已判定不修） |
| **P3** | CI 加定时跑（每日 cron） | 15min | 发现间歇性回归 |

---

## 八、结论

这是一个**纪律性极强**的项目。35 个 bug 全带四段工作流、130 个离线测试、AST 契约断言、CHANGELOG 覆盖校验——这些不是"个人项目"会有的东西，这是**有工程素养的人在认真做产品**。

如果我要给一个刚入职的工程师看"个人项目能做到什么程度"，我会给他看这个仓库。

**最值得学习的三点**：
1. **子进程边界**——计算与 UI 的隔离是本项目最干净的设计决策
2. **测试工程化**——拒网守卫自测、AST 契约断言、PIN 安全扫描，测试不只验"能跑"还验"不会悄悄坏"
3. **BUGLIST 四段工作流**——每个 bug 都有"梳理→确认→修复→验证"，问题生命周期可追溯

**最该避免的一点**：
- **数据层反向依赖 UI 框架**——`storage.py` 认识 `streamlit`，这是选型时没意识到的债，现在改它成本高

---

**总评：8.3/10 — 一个完成度远超个人项目水准、纪律性极强的定投决策系统。代码像一群有纪律的工程师在改，不是一个人在堆。**

---

## 附录：与第一轮 CR（2026-08-24）对比

| 项 | 第一轮（2026-08-24） | 第二轮（2026-08-25） |
|---|---|---|
| 评审时 HEAD | `5b0bb9d`（P1/P2 修复前） | `f0bce5d`（P1/P2 修复后 + 引擎拆分后） |
| P1-1 往返消除 | 🔴 识别 | ✅ 已落地（`build_wide_rows` + 删 `parse_wide_table`） |
| P1-2 schema DRY | 🔴 识别 | ✅ 已落地（`build_tx_row` / `build_obs_row`） |
| P2-1 curves sys.path | 🟡 识别 | ✅ 已落地（`src/market_cache.py`） |
| P2-3 BTC 兜底 | 🟡 识别 | ✅ 已落地（`btc_last.json` + stale 标注） |
| 引擎拆分 | 建议切分 | ✅ 已落地（6 模块线性 DAG） |
| 新发现问题 | — | P1-NEW-1/2, P2-NEW-1~4 |
| 综合评分 | 未评分 | 8.3/10 |

# 工程架构说明书（概要版）

> 【活文档 · 更新时机：**仅顶层架构变更**（换技术栈/存储/部署、改数据流、调目录、动链路）时更新；实现细节变动只更新详设，不动本文】
> **本文件是本项目顶层架构的唯一事实源。** 首版 2026-08-17，2026-08-18 拆分为「概要 + 详设」两份。
> 面向所有读者（含非技术）：第一次出现的概念都解释。实现细节、设计动机、踩坑记录 → [ARCHITECTURE-DETAIL.md](ARCHITECTURE-DETAIL.md)（同一事实只写一处，本文不重复）。
> 问题与缺陷不写在这里，走 [BUGLIST.md](BUGLIST.md)。

**改架构的人必读的维护约定：**

1. **顶层架构一有变动，本文件同期更新** —— 不是事后补，是同一次改动里一起改。
2. **本文只写稳定事实。** 行号允许出现，但必须「锚点 + 行号」双写、且所在节标复核日期；代码改动后按锚点找回、同期修行号。
3. **术语第一次出现要解释**，读者不一定是写代码的人。
4. **顶层变更在文末「变更记录」加一行**：日期 / 改了什么 / 为什么。文档与代码冲突时代码为准，然后立刻修文档。

---

## 1. 这是什么

标普500 / 纳指100 / 黄金三资产**动态定投决策系统**：每天看一眼，告诉你今天该投多少钱、三样资产各配多少。两个使用入口，**共用同一套计算引擎和数据**：

| 入口 | 文件 | 说明 |
|---|---|---|
| **Web 决策台** | `app.py` | Streamlit 网页，多用户，云端存储 |
| **Claude Skill** | `X:\coding\skills\projects\sp500-nasdaq100-gold-dca\SKILL.md`（通过目录联接挂到 `~/.claude/skills`） | 对话式每日建议 |

两个入口的业务写操作（记账 / 预算覆盖）走**同一业务层**：Web 侧由 `storage.py` 直接承担，Skill 侧经 `scripts/dca_action.py` CLI 薄壳（subprocess 调同一 `storage.py`）——两边口径一致，不会分叉。

## 2. 技术栈全表

| 层 | 用的技术 | 一句话说明 |
|---|---|---|
| 语言 | **Python 3.14.4**（本机 `.venv` 实装） | — |
| 网页框架 | **Streamlit 1.61.1** | 把 Python 脚本直接变成网页的工具。**不是** Django/Flask 那种传统框架——每次交互整个脚本从头重跑，模型详解见详设 §1 |
| 前端 | Streamlit 自带（内部是 React） | 不写 HTML/JS。定制样式靠 `st.markdown(..., unsafe_allow_html=True)` 注入 CSS |
| 数据处理 | pandas 3.0.5 + numpy 2.5.2 | 表格运算。⚠️ pandas 3 是大版本，与 pandas 2 有不兼容改动 |
| 数据存储 | **Google Sheets**（`st-gsheets-connection` + `gspread 5.12.4`） | 把一个 Google 表格当数据库用；只能整表读、整表写，代价见详设 §3 |
| 存储回退 | 本地 CSV 文件 | 没配 Google 凭据时自动降级成单机模式 |
| 行情数据 | Yahoo Finance Chart v8 接口（`urllib` 直连）+ `yfinance 1.6.0` 兜底 + 东方财富 push2（`curl` 子进程） | 三个来源，**都没有 API key，都是非官方接口**；降级链见详设 §4 |
| 用户认证 | 自己写的"名字 + PIN" | PIN 用 PBKDF2-HMAC-SHA256（20 万迭代 + 每账号随机盐）存在 users 表；旧 SHA256 账号登录成功时自动迁移 |
| 计算引擎 | 独立 Python 脚本 + **subprocess**（子进程隔离） | 见 §3 架构图；为什么这么做见详设 §2 |
| 部署（生产） | **Streamlit Community Cloud** | 推送到 GitHub main 分支自动重新部署 |
| 临时外发 | ngrok 固定域名 | 本机开着时把 8501 端口发到公网 |
| 版本控制 | git + GitHub **公开**仓库（用户有意设置） | `Behappybehealth/sp500-nasdaq100-gold-dca` |
| 回归测试 | pytest 9.1.1 + Streamlit AppTest | 三层全离线回归：引擎纯函数 / storage 安全路径 / 整页渲染冒烟；离线由 autouse 拒网守卫强制并自测 |
| 持续集成 | GitHub Actions | push `main` 自动跑 Windows/Python 3.14 精确锁定环境 + Linux/Python 3.12 Cloud 范围环境；不读取 secrets |
| 代码格式 | ruff | 有格式化痕迹，但**未进 CI 强制** |
| 运行日志 | 标准库 `logging`（零第三方） | `dca.*` 子树，stderr + `logs/dca.log`（1 MB × 3 轮转）双落点；配置集中 `src/obs.py`，见 §11 |

（版本复核于 2026-08-20；本机实装与精确版本见 `requirements-dev.lock`）

## 3. 核心架构：三层 + 一个边界

```
                        ┌─────────────────────────────────┐
   浏览器（用户）  ←───→ │  Streamlit Community Cloud      │
                        │  一个 Python 进程服务所有用户     │
                        │                                 │
                        │    app.py（70 行，纯装配层）      │
                        │    认证/侧栏/6 tab 全部在 src/   │
                        └───┬──────────────────┬──────────┘
                            │                  │
              subprocess    │                  │  import
              （子进程）     │                  │
                            ▼                  ▼
        ┌───────────────────────────┐   ┌──────────────────┐
        │ scripts/dca_calculator.py │   │   storage.py     │
        │ 计算引擎入口（240 行）     │   │  存储层（622 行） │
        │ + 5 个兄弟模块（1266 行）  │   │                  │
        │                           │   │                  │
        │ 读 data/config.json       │   │ 优先 Google Sheets│
        │ 读记账数据（--user 时      │◄──┤ 无凭据→本地 CSV   │
        │  读 data/users/<user>/）  │ 把云端数据落盘到      │
        │ 读 data/market_history/   │ data/users/<user>/   │
        │ 抓 Yahoo / 东财行情        │ （每用户独立目录）     │
        │ 输出 JSON 到标准输出        │                     │
        └───────────┬───────────────┘   └────────┬─────────┘
                    │                            │
                    ▼                            ▼
        ┌────────────────────┐        ┌──────────────────────┐
        │ Yahoo Chart v8     │        │  Google Sheets       │
        │ 东方财富 push2      │        │  4 个主表 + _bak 快照 │
        │ （无 key、无额度保证）│        │  users / transactions │
        └────────────────────┘        │  observations /       │
                                      │  budget_overrides     │
                                      └──────────────────────┘
```

**三层 + 一个边界**：`app.py`（UI + 业务逻辑，耦合较紧）→ 通过 subprocess 隔离 `dca_calculator.py`（纯计算，拆为 5 个兄弟模块 + 薄入口）、通过 import 使用 `storage.py`（数据层）。子进程边界是本项目最干净的设计：改计算不影响 UI，反之亦然。

**拆分已完成**（方案存档见 `docs/plans/app-split-design.md`）：启动路径逻辑收编 `src/context.py`，服务函数（模型调用 / 行情抓取 / 曲线计算）在 `src/services/`，全局 CSS / 遮罩 / 侧栏 / 认证在 `src/ui/`，六个 tab 在 `src/tabs/`；所有模块数据显式收参，不读 app.py 模块级全局。app.py 1559→**70 行纯装配层**（import → build_paths → setup_logging → storage.init → 认证门闸一行 → 侧栏一行 → 6 个 tab render 调用）。**引擎也已拆分**：`scripts/dca_calculator.py`（原 1424 行单文件）拆为 5 个兄弟模块 + 薄入口（线性依赖 DAG，无循环引用），所有公共符号通过 re-export 保持 `import dca_calculator as eng` 全部调用方零改动。

## 4. 数据流：一次"打开页面看今日建议"

```
① 用户输入名字 + PIN 点登录
      ↓
② storage.authenticate() 去 Google Sheets 的 users 表校验
   （一次新鲜读完成「锁定 / 存在性 / 激活态 / PIN」四重判断：
     锁定期内对错都拒；连续失败 5 次锁 15 分钟；旧 sha256 账号
     验证通过即自动迁移为 PBKDF2；顺手返回最新用户名列表刷新会话缓存）
      ↓ 通过
③ storage.sync_local(用户名)
      把 Google Sheets 里【这个用户】的成交记录，写到本地
      data/users/<用户名>/ （每用户独立目录，覆盖前带时间戳轮转留底 10 份）
      ↓
④ 侧边栏调用 run_model(None, CURRENT_USER, _paths)   ← 缓存键含用户身份
      → 启动子进程 dca_calculator.py --base-dir <项目目录> --user <用户名>
      （run_model 等三个服务函数在 src/services/，_paths 来自 src/context.py）
      ↓
⑤ 引擎读 data/users/<用户名>/transactions.csv + data/config.json
      （config 与行情缓存保持共享；记账数据按用户分目录）
      → 增量抓行情（只抓缓存里缺的那几天）
      → 算评分、算金额、算比例
      → 把结果 print 成一大段 JSON
      ↓
⑥ app.py 收下 JSON，拆成 result / dec / ms / pf 四个变量
      → 结果被 st.cache_data 缓存 900 秒，缓存键 =（金额, 用户）
      ↓
⑦ 6 个 Tab 依次渲染，直接用上面那四个变量
      ↓
⑧ 用户在 Tab3 回报成交 → storage.append_row() 写回 Google Sheets
```

## 5. 三条业务链路

### A. 认证链（`src/ui/auth.py` 的 `require_user()`；app.py 侧仅 :38 一行调用）

三阶段状态机，全部走 `st.session_state`：`login`（名字+PIN 校验）→ `activate`（未激活账号首次设 PIN）→ `bootstrap`（users 表为空时首个注册者自动成为 admin）。门闸用 `st.stop()` 拦住未登录用户，后续代码根本不执行。两段式防残留设计与其踩过的三轮坑，见详设 §6（**不要轻易改动**）。

### B. 决策链（`src/services/` + `src/ui/sidebar.py` 执行）

```
sidebar.render() ──→ run_model(None) ──subprocess──→ dca_calculator.py ──→ JSON
                          └─→ result / dec / ms / pf 收口为 Decision 返回值 ──→ 各 tab 消费
```

注意：侧栏先 `run_model(None)` 自动定额跑一遍；用户在表单（`amount_form`）里提交金额后按新金额重跑——重跑趟命中引擎行情快照（`data/quote_snapshot.json`，TTL 600 秒），跳过重复抓价，近即时返回。细节见详设 §7。

### C. 记账链（tab3 写 → tab4 读；`src/tabs/records.py` / `src/tabs/history.py`）

```
tab3  用户回报成交 → session_state["pending_tx"] 暂存 → 复述确认 → storage.append_row("transactions")
      主动跳过     → session_state["pending_obs"]              → storage.append_row("observations")
tab4  storage.read_rows() 两张表原样展示
```

append_row 对 transactions 默认拦"同日同资产同方向"重复（防时区错位/手滑的重复投），撞重时 UI 挂警告、用户显式点「仍要写入」后 `force=True` 放行；日期默认与业务月份一律取 `biz_today()`（Asia/Shanghai 固定 UTC+8，与容器 UTC 时区解耦），坏格式日期提交即拒收。

tab4（26 行）是这条链的读侧，业务上和 tab3 是一件事。

（行号复核于 2026-08-19）

## 6. app.py 一次渲染的时序

`app.py` **不是模块，是一个从头跑到尾的脚本**（Streamlit 重跑模型所致，详解见详设 §1）：

```
① 13–35  import → build_paths()（启动逻辑在 src/context.py）→ setup_logging()（:31，运行日志，必须在 storage.init 之前）→ storage.init() → set_page_config
② 38–39  注入全局 CSS（inject_css()，样式本体在 src/ui/styles.py）
③ 41–42  认证门闸：CURRENT_USER = auth.require_user()（本体在 src/ui/auth.py；未登录 st.stop()，下面的代码根本不执行）
④ 44–46  侧边栏：sidebar.render(_paths, CURRENT_USER)（本体在 src/ui/sidebar.py），返回 Decision 解包出 result/dec/ms/pf
⑤ 48–58  声明 6 个 tab（st.tabs 在 :49）
⑥ 60–70  渲染 6 个 tab（全部 src/tabs/ 的 render() 调用）
```

**关键点：app.py 已是纯装配层**——每段只剩一行调用 + 指针注释，业务全在 `src/` 对应模块。模型执行点在 `src/ui/sidebar.py` render() 内（首跑 :124、表单提交后金额重跑 :235）；决策结果收口为 `Decision` 返回值，由 app.py 解包显式传给各 tab。

（行号复核于 2026-08-21）

## 7. 六个 Tab 的职责

| tab | 位置 | 行数 | 业务职责 | 依赖 |
|---|---|---:|---|---|
| 🎯 今日模拟 | `src/tabs/today.py` | 99 | 今日建议金额/部署系数/三资产分配/三档执行方案；行情不可用于决策时顶部横幅说明"本次不出金额"（原因取引擎 `freshness.reason`，不写死天数） | render(tab1, result, dec, ms, ASSETS) |
| 📊 持仓与曲线 | `src/tabs/holdings.py` | 78 | 持仓汇总、估值、浮盈亏、XIRR、净值曲线（汇率不可用时估值显式置空） | render(tab2, result, pf, ASSETS, _paths, CURRENT_USER) |
| ✍️ 记账 | `src/tabs/records.py` | 182 | 回报成交 / 主动跳过，二次确认后落库；同日同资产同方向去重，显式确认后放行 | render(tab3, result, dec, ASSETS, CURRENT_USER) |
| 📜 历史记录 | `src/tabs/history.py` | 26 | 回读 transactions / observations | render(tab4, CURRENT_USER) |
| 🧪 回测结果 | `src/tabs/backtest.py` | 249 | 5 段静态回测报告（全部读 `backtest/*.json`；内部段界见详设 §10） | render(tab5, BACKTEST_DIR) |
| 📖 策略说明 | `src/tabs/strategy_doc.py` | 18 | 读 `strategy/core-strategy.md` 渲染（唯一事实源） | render(tab6, CODE_DIR) |

（行号复核于 2026-08-19）

## 8. 数据在哪：四张主表 + 快照 + 本地回退

**Google Sheets（多用户唯一事实源）**——四张主表 + 每表一条写前快照：

| 工作表 | 字段 |
|---|---|
| `users` | `name, pin_hash, salt, hash_algo, role, fail_count, locked_until, created_at`（旧 4 列行由 `_read_ws` 补空串兼容） |
| `transactions` | 成交记录，含 `user` 列 |
| `observations` | 跳过/观察记录，含 `user` 列 |
| `budget_overrides` | 月度预算覆盖，含 `user` 列 |
| `<任意表>_bak` | 写前快照（滚动单份）：覆写主表前先把现内容推到这里，快照失败则放弃写入 |

所有数据表都带 `user` 列做行级隔离。 Sheets 只能整表读、整表写——这个约束与写前快照的动机见详设 §3。

**本地回退与缓存**：

- 无 Google 凭据 → 自动降级单机模式，读写 `data/{transactions,observations}.csv` + `data/budget_overrides.json`
- 云端模式每用户落盘缓存 `data/users/<user>/`（`sync_local` 生成，覆盖前轮转留底 10 份），供引擎 `--user` 读取
- **行情缓存 `data/market_history/*.csv`（6 个文件，两列 `date,close`，增量更新）——入库是刻意的**，让 Cloud 部署不用冷启动重抓十年数据。⚠️ 这是本仓库最宝贵的资产，别删；增量机制的代价见详设 §4

## 9. 目录逐个说明

> 速查版（依赖图 + 文件树）见 [`STRUCTURE.md`](../STRUCTURE.md)；本节是带「谁读它/入库」列的详细版。

### 9.1 根目录

| 路径 | 行数 | 是什么 | 谁读它 | 入库 |
|---|---:|---|---|:---:|
| `app.py` | 70 | Streamlit 主程序，**纯装配层**：import → build_paths → setup_logging → storage.init → 认证一行 → 侧栏一行 → 6 个 tab render 调用 | Streamlit 直接执行 | ✅ |
| `src/` | 2137 | **业务层**（app.py 只留装配）：`context.py`（73，启动上下文 `Paths`/`Decision`/`build_paths`）+ `dates.py`（20，业务"今天"唯一定义 `biz_today()`，Asia/Shanghai 固定 UTC+8，与引擎同规则双实现）+ `obs.py`（62，运行日志配置：幂等 `setup_logging()`、stderr + 轮转文件双落点；**只配 handler 不提供 emitter**，各模块自己 `getLogger("dca.<频道>")`，数据层因此不必反向 import `src/`）+ `state.py`（113，**session_state 键登记表**：11 个键名常量各标注所属链、生命周期与注意事项，`ALL_KEYS` 供测试断言登记表不漂移，`invalidate_sync()` 收敛“触发重同步”这条跨模块协议；只定义名字与协议、不封装每个键的读写）+ `services/`（`model.py` 67 模型调用 / `quotes.py` 87 行情抓取 / `curves.py` 102 曲线数据）+ `ui/`（`styles.py` 185 全局 CSS / `overlays.py` 59 三遮罩 / `sidebar.py` 339 侧栏，返回 `Decision` / `auth.py` 365 认证门闸，`require_user()`）+ `tabs/`（`today.py` 99 / `holdings.py` 78 / `records.py` 183 / `history.py` 26 / `backtest.py` 249 / `strategy_doc.py` 18，各暴露 `render(tab, ...)` 显式收参）；不读 app.py 模块级全局 | `app.py` import | ✅ |
| `storage.py` | 622 | 存储层。所有 Google Sheets 读写都走它（含写前快照、PBKDF2 认证、成交同日同资产同方向去重，`force=True` 显式放行）；19 个公开接口明细见详设 §9 | `app.py` import | ✅ |
| `requirements.txt` | 16 | **Cloud/Linux 可安装范围**：6 个直接依赖都有下界与大版本上界，不强钉本机 Windows wheel | Streamlit Cloud + CI Linux/Python 3.12 腿 | ✅ |
| `requirements-dev.lock` | 83 | **Windows/Python 3.14 开发机精确锁定**：完整 `pip freeze`，含 pytest 与全部间接依赖 | 本机复现 + CI Windows/Python 3.14 腿 | ✅ |
| `pytest.ini` | 7 | pytest 只收 `tests/`，不把归档回测脚本当测试收集 | pytest | ✅ |
| `tests/` | — | 全离线回归 **130 条**：引擎纯函数 46 / storage 本地与 Sheets 安全路径 25 / AppTest 整页冒烟 20 / 拒网守卫自测 8 / 运行日志 7 / 状态键登记表 17 / 弃用 API 2 / 备份脚本源码不变量 5；离线由 `conftest` autouse 守卫强制，日志由同处 autouse 的 `_quarantine_logging` 隔离（否则 AppTest 会把假异常写进工作树 `logs/dca.log`），fixture 全虚构 | pytest / CI | ✅ |
| `.github/workflows/ci.yml` | — | push `main` 自动跑两条 pytest 腿；无 secrets、无行情网络依赖 | GitHub Actions | ✅ |
| `CHANGELOG.md` | — | **全量改动的人读版流水**：每 commit 一行带 `HH:MM:SS` 时刻（取自 git），由 `scripts/changelog.py` 生成/校验 | 人 | ✅ |
| `start-app.bat` | — | 本机双击启动 | 你 | ✅ |
| `CLAUDE.md` | — | 给 AI 编程助手的项目说明 | AI 助手 | ✅ |
| `README.md` | — | 项目自述 | 人 | ✅ |

### 9.2 `scripts/` — 计算引擎与工具

| 路径 | 行数 | 说明 |
|---|---:|---|
| `scripts/dca_calculator.py` | 240 | **策略大脑入口**。薄入口：`main()` + stdout/stderr 编码修正 + re-export 全部公共符号（保 `import dca_calculator as eng` 零改动）；完全独立可单跑，不依赖 Streamlit。输入 = CSV + config，输出 = JSON（18 个顶层键）；参数与键明细见详设 §11 |
| `scripts/dca_types.py` | 211 | 数据结构、工具函数与记账数据加载：`DEFAULT_CONFIG`/`_BIZ_TZ`/`biz_today()`/`is_iso_date()`/`utc_today()`/`Transaction`/`read_json`/`resolve_monthly_budget`/`as_float`/`read_transactions`/`read_observations`/`trading_days_in_month`/`monthly_budget_status` |
| `scripts/dca_market.py` | 524 | 行情抓取、缓存 I/O 与汇率获取：`fetch_json`/`fetch_chart`/`metrics_from_closes`/`pairs_from_chart_result`/`sanitize_symbol`/`cache_file_for`/`load_cached_closes`/`close_at_or_before`/`save_cached_closes`（三道护栏）/`load_market_live`/`save_market_live`/`split_live_bars`/`merge_live_bars`/`_yfinance_closes`/`get_symbol_history`/`fetch_history`（并发）/`fetch_usdcny`/`fetch_usdtusd`/`load_fx_last`/`save_fx_last`/`_fx_entry`/`load_quote_snapshot`/`save_quote_snapshot`/`market_symbol_for_asset` |
| `scripts/dca_portfolio.py` | 131 | 组合持仓计算：`xnpv`/`xirr`/`portfolio_summary`（含价格代理查找、汇率选择、XIRR 年化） |
| `scripts/dca_scoring.py` | 247 | 评分模型与决策引擎：`clip`/`DEFAULT_MODEL`/`asset_score`/`level_label`/`neutral_weights`/`score_based_weights`/`market_freshness`（新鲜度闸）/`build_decision`（评分→部署系数→金额→权重倾斜→比例） |
| `scripts/dca_table.py` | 153 | 宽表结构化行与 markdown 渲染：`asset_note`/`WIDE_TABLE_HEADER`/`_money`/`_pct`/`_num`/`_xirr_cell`/`build_wide_rows`（list[dict] 结构化中间体）/`render_wide_table`（markdown） |
| `scripts/dca_action.py` | 203 | 业务动作 CLI（`record tx` / `record obs` / `override`）：Skill 入口经它与 Web 共用 storage 业务层；shares 可按金额自动换算，sheets 模式写后自动 `sync_local` 刷新落盘缓存；同日同向撞重报错、`--force` 显式放行 |
| `scripts/changelog.py` | 115 | CHANGELOG 维护工具：`add <hash>` 从 git 取提交时刻生成行草稿；`--check` 校验每个 commit 都有行且时刻与 git 一致（CLAUDE.md 第 12 条的配套） |

### 9.3 `data/` — 数据目录（引擎唯一的数据来源）

| 路径 | 是什么 | 入库 | 说明 |
|---|---|:---:|---|
| `data/config.json` | 策略参数与资产定义（权重、区间、评分系数） | ✅ | 改策略参数改这里 |
| `data/users/<用户>/` | **云端模式的每用户落盘缓存**（transactions/observations/budget_overrides） | ❌ | 由 `sync_local` 从 Sheets 覆写生成；覆盖前带时间戳轮转留底 10 份（`*.YYYYMMDD-HHMMSS.localbak`） |
| `data/transactions.csv` | **成交记录**（真实持仓的账本） | ❌ | 仅本地单机模式使用；云端模式已改 `data/users/<user>/` |
| `data/observations.csv` | 跳过/观察记录 | ❌ | 同上 |
| `data/budget_overrides.json` | 按月覆盖预算 | ❌ | 同上 |
| `data/fx_last.json` | 汇率上次成功值（分字段带 `fetched_at`） | ❌ | 抓取成功时刷新；实时失败时引擎读它兜底并在 `fx` 段标 `live:false` + `as_of` |
| `data/market_live.json` | **当日未收盘 K 线**（每标的 `{bars, quote_time, fetched_at}`） | ❌ | 盘中记账时"我看到的那个价"的落点。只存仍属 `utc_today()` 的 bar；加载时 merge 进序列但 **csv 优先**，定稿值落库即自动顶掉。本次没抓到当日 bar 的可信信息时不动存量（不会把陈旧值伪装成刚抓的） |
| `data/*.localbak` | 轮转留底文件 | ❌ | 带时间戳的真轮转（覆盖前自动留底 10 份） |
| `data/market_history/*.csv` | **已收盘定稿收盘价**，6 个文件，两列（`date,close`） | ✅ | **入库是刻意的** —— 让 Cloud 部署不用冷启动重抓十年数据。每次抓取回退 5 天重抓，故最近 5 天的值可能被数据源事后修正（正常，见 BUG-030/031） |

**行情缓存 6 个文件**：`_GSPC.csv`（标普指数）、`SPY.csv`（标普 ETF）、`_NDX.csv`（纳指）、`QQQ.csv`（纳指 ETF）、`GC_F.csv`（黄金期货）、`XAUT_USD.csv`（黄金代币）。孤儿 `GLD.csv`（不在抓取名单、冻结在 2026-08-10）已从仓库移除，取回：`git show f1ed967:data/market_history/GLD.csv`。

### 9.4 `backtest/` — 回测（一次性产物，非运行时依赖）

| 路径 | 说明 |
|---|---|
| `backtest_dca.py` / `backtest_single.py` / `backtest_compare3.py` | **归档的一次性脚本**：均按 `__file__` 相对定位，可随仓库搬移后重跑；各自重写了历史决策链，故不作回归测试载体。重跑会覆盖邻接 JSON，须先复制留底 |
| `results_compare3.json` | **冻结产物**；Tab5 第一段读它（三策略对比） |
| `results_single_compare.json` | **冻结产物**；Tab5 第三段读它（单品种动态 vs 固定） |
| `results_rolling.json` | **冻结产物**；Tab5 第三段四张滚动表 + 第四段横向对比读它（从 app.py 硬编码无损导出，33 行 × 338 格与原字面量逐格相等） |
| `results.json` / `results_single.json` / `results.md` / `compare3.md` | 冻结的中间产物与文字报告，应用不读 |

### 9.5 `strategy/` — 策略文档

| 路径 | 说明 |
|---|---|
| `strategy/core-strategy.md` | 策略说明**唯一事实源**。Tab6 启动时读它直接渲染，改文档即改页面 |

### 9.6 `deploy/` — 部署与外发

> **2026-08-17 已清理**：Docker 那套（`Dockerfile` / `docker-compose.yml` / `nginx.conf` / `setup_user.sh` / `streamlit-config.toml`）已删除 —— 它从未成功构建过一次（自 `574c7a7` 初始提交起一行未改），却被标为"唯一事实源"。
> 取回：`git show 574c7a7:deploy/Dockerfile`。删除理由与「将来重启 Docker 的必守清单」见 `deploy/DEPLOY.md` 第 5 节。

| 路径 | 状态 | 说明 |
|---|---|---|
| `DEPLOY.md` | ✅ 已重写 | 只写真实在用的三条路径：Cloud（生产）/ 本机 / ngrok |
| `start-dca-tunnel.bat` | ✅ 活的 | ngrok 外发。**只能写 ASCII** —— cmd 按 OEM 码页（936）读批处理，UTF-8 中文注释会被当乱码命令执行。中文说明写进 DEPLOY.md |
| `bin/ngrok.exe` | ✅ 33 MB | **不入库**，删了只能重下。脚本靠 `%~dp0bin\ngrok.exe` 相对定位 |

### 9.7 `docs/` 与 `.streamlit/`

> **活文档**随代码同步更新、可以信赖；**冻文档**是历史快照，只增不改。各文档头部标 `【活·更新时机：…】`；行为变更的 commit 须同期核对相关活文档（与 CLAUDE.md 第 11 条互为表里）。

**活文档**：

| 路径 | 说明 | 入库 |
|---|---|:---:|
| `docs/ARCHITECTURE.md` | **本文件**。顶层架构唯一事实源 | ✅ |
| `docs/ARCHITECTURE-DETAIL.md` | **架构详设**：实现细节、设计动机、踩坑记录 | ✅ |
| `docs/BUGLIST.md` | **问题台账**。每条走「梳理 → 1对1确认 → 修复 → 验证」四段 | ✅ |
| `.streamlit/config.toml` | 主题配色 | ✅ |
| `.streamlit/secrets.toml` | **GCP 服务账号凭据**，2600 字节 | ❌ |
| `.streamlit/secrets.toml.example` | 模板 | ✅ |

**冻文档**（历史快照，只增不改）：

| 路径 | 说明 |
|---|---|
| `docs/plans/app-split-design.md` | app.py 拆分方案（施工图纸，已执行完毕） |
| `docs/plans/project-audit-2026-08-17.md` | 2026-08-17 全量审计原始快照（26 条问题的出处） |
| `docs/plans/architecture-and-p0-explained.md` | ARCHITECTURE / BUGLIST 的前身，已被拆分取代 |
| `docs/plans/distributed-pondering-puppy.md` | 回测模型实施计划（已执行完） |
| `docs/plans/proud-discovering-kitten.md` | 完整升级计划（历史） |
| `docs/plans/toasty-yawning-dewdrop.md` | Skill 改版计划（历史） |

（行数复核于 2026-08-18）

## 10. 数据口径与线上地址

**数据口径**：

- **交易本位**：USDT（标识 `"U"`）
- **代码**：标普500 → `SPY`，纳指100 → `QQQ`，黄金 → `XAUT`
- **估值**：SPY/QQQ 用 Yahoo 实时价 × USD/CNY；XAUT 用 `XAUT-USD` × U/CNY
- **月度预算**：默认 30000 RMB，可按月覆盖（`data/budget_overrides.json`）
- **中性权重**：SP500 35% / NDX100 45% / 黄金 20%

**线上地址**：

| 用途 | 地址 |
|---|---|
| 生产 | https://dca365.streamlit.app/ |
| ngrok 临时外发 | https://sudoku-manhood-argue.ngrok-free.dev |

平台侧必须配的三项（`share.streamlit.io` → 应用 ⋮ → Settings）：

- **Secrets** —— GCP 凭据，内容同本机 `.streamlit/secrets.toml`。**不在 git 里**，换机器/重建应用要手动贴
- **General → App URL** —— 自定义子域 `dca365`
- **Sharing → public** —— 否则访问者要先登录有权限的 Streamlit 账号，家人打不开

> 应用是 public 的，意味着**应用内的「名字 + PIN」门闸是唯一防线**（fail-closed：凭据缺失/损坏即拒启动；PBKDF2 + 连续失败锁定）。

## 11. 日志体系 —— 三层分野

| 层 | 内容 | 落点 | 入库 |
|---|---|---|:---:|
| 机器流水 | 每个 commit 的精确时刻（epoch 存于 `.git/logs/`，人读格式用 `git log --date=format-local`） | git 自身 | — |
| 改动日志 | 人读版流水：每 commit 一行、**带 HH:MM:SS 时刻**（取自 git，非手写） | 根目录 `CHANGELOG.md`，由 `scripts/changelog.py` 生成/校验 | ✅ |
| 运行日志 | 应用运行时的**失败与降级**事件：认证异常/结果码、`sync_local` 失败、Sheets 读写与快照失败、引擎非零退出与输出解析失败、行情降级 | 双落点：**stderr**（Cloud 日志面板唯一可见处）+ `logs/dca.log`（1 MB × 3 轮转；`*.log` 不入库）。配置在 `src/obs.py`，`app.py:31` 启动时调一次 | ❌ |

运行日志的三条设计约束（都是防止修出新问题，实测依据见 BUG-017 验证结果）：

- **频道名写死 `dca.*`，不用 `__name__`**——`storage.py` 是顶层模块，`__name__` 就是 `"storage"`，不在 `dca` 子树下；用 `__name__` 就只能去配 root logger，那会把 gspread / urllib3 / streamlit 的噪声全引进来
- **`setup_logging()` 幂等**——Streamlit 每次交互整脚本重跑，不去重则日志行数随交互次数翻倍
- **文件 handler 必带轮转**——BUG-017 原文的另一半就是"日志无上限写满磁盘"，不轮转等于把它请回来

⚠️ **两条边界，不假装解决**：① `logs/` 落点只对**本机 / 长期部署**有意义，Streamlit Cloud 容器重启即丢文件系统，那边只有 stderr 面板且只留近期；② **零告警**——出事仍然要有人去开页面才知道（外部探针见 [BUGLIST](BUGLIST.md) 的 BUG-034，已判定不修并附重开条件）。所以现有能力是"出事当场能查真因"，不是"长期留存 + 主动告警"。

---

# 变更记录（只记顶层架构变更；细节变动见 CHANGELOG.md 与详设）

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-08-17 | **首版建立。** 从 `docs/plans/architecture-and-p0-explained.md` 拆出架构部分；问题部分转入 [BUGLIST.md](BUGLIST.md)（原文一条不丢，全部按 `BUG-0XX` 编号登记） | 一份文档同时讲架构和缺陷，两者更新节奏不同，必然漂移 |
| 2026-08-17 | 记录三处**已修正的文档不实**：① CLAUDE.md 与旧 DEPLOY.md 都称 Tab6 读 `strategy/core-strategy.md`，实测 `grep strategy app.py` **零命中**；② 旧 Dockerfile 声明 `python:3.12-slim`，本机实测 **Python 3.14.4**；③ 旧 DEPLOY.md 称"每个用户有独立容器和数据目录，完全隔离"，实际所有用户共用一个 `data/transactions.csv` 和一块进程级缓存 | 文档说的和代码做的不一致，比没有文档更危险 |
| 2026-08-17 | `deploy/` 删除 5 个 Docker 死文件（`Dockerfile` / `docker-compose.yml` / `nginx.conf` / `setup_user.sh` / `streamlit-config.toml`），§3.6 改写 | **主修 `BUG-012`**（Docker 那套从未成功构建过，自初始提交零迭代，却被标为"唯一事实源"）。**因为下面三个问题的成因全部落在被删的文件里，同一个动作连带修复了**：`BUG-005`（GCP 私钥被 `Dockerfile:21` 的 `COPY .streamlit/` 打进镜像层）、`BUG-013`（容器隔离与应用内登录两套多用户实现互相抵消）、`BUG-014`（`setup_user.sh:90` 的 sed 会把 nginx location 插到 server 块外面，加一个用户炸掉所有用户）。四条记录连带关系与验证结果都在 BUGLIST 里，**一条都没删** |
| 2026-08-18 | **P0 清舱（BUG-001~004，commit `a1707a6` + `f02ff22`）**：① 认证门闸 fail-closed——`AUTH_MODE` 默认 `sheets`，secrets 缺失/损坏即拒启动，单机必须显式 `DCA_AUTH_MODE=local`；② 多租户隔离补齐另一半——`run_model` 缓存键含用户、引擎新增 `--user` 读 `data/users/<user>/`、`sync_local` 分目录落盘；③ 存储层 "empty ≠ error"——读故障抛 `SheetReadError`、写前快照 `<表名>_bak`（快照失败放弃写入）、本地轮转留底 10 份；④ PIN 升级 PBKDF2+随机盐、连续失败 5 次锁 15 分钟、旧 sha256 账号登录自动迁移、新 PIN 强制 6-8 位 | 四条 P0 全部经 1 对 1 确认后施工，43 项离线假连接测试全过 + 引擎双模式回归 + AppTest 双模式冒烟；确认记录/改动清单/验证输出均已回填 BUGLIST |
| 2026-08-18 | **BUG-026+021 修复**：`strategy/core-strategy.md` 全量重写为 184 行合并版（技术骨架 + 原 Tab6 独有的家人友好开场、§4 闭环图、§8 回测结论诚实版、§12 隐私真话版）；`app.py` Tab6 删掉 75 行内嵌副本改为读文件渲染（1967–1974），app.py 2041→1974 行 | 同一份策略说明两个副本必然漂移（026）；隐私声明写的是"数据不上传任何地方"，实际全部存 Google Sheets（021）。现在 Tab6 直接渲染唯一事实源，改文档即改页面 |
| 2026-08-18 | **BUG-023 修复**：删三处死定义——`verify_user`（storage.py，全项目零调用）、`append_csv`（app.py，定义后从未调用）、`OBS_CSV`（app.py，定义后从未使用）；孤儿 `GLD.csv` 从仓库移除（git 可取回）。storage.py 603→594 行、app.py 1974→1964 行、公开接口 20→19、模块级全局 9→8、行情缓存 7→6 个文件 | 死物让人误以为功能还在，顺着改会改到空气；孤儿文件不留中间态。修复路径与 GLD 删除均经用户拍板 |
| 2026-08-18 | **BUG-025 修复**：Tab5 五块硬编码回测数据（sp500/ndx/gold/hs300 四张滚动表 + 四标的横向对比，共 33 行 × 338 格）AST 无损导出为 `backtest/results_rolling.json`；app.py 改为统一读文件 + 缺失时 warning 优雅降级；结尾 caption 从失效的 `backtest-dca-5y/` 改指 `backtest/`。app.py 1964→1559 行（tab5 638→233 行） | 代码仓库不放数据：改回测不再触发代码部署；单一供数方式消除"两个事实源哪个新"的问题 |
| 2026-08-18 | **日志体系落成（用户拍板）**：① CHANGELOG.md 全量回填时刻——每条 `HH:MM:SS` 取自 git commit 时间，组内改按时刻新在上；② 新增 `scripts/changelog.py`（`add <hash>` 生成行、`--check` 校验 commit 全覆盖且时刻一致）；③ 新建 `logs/` 目录定为**运行日志**落点（`*.log` 不入库，`.gitkeep` 占位；Cloud 容器重启即失的限制已写入本文 §11） | 用户在 `.git/logs/refs/heads/main` 里只看到 epoch 秒数、人读层 CHANGELOG 又只有日期——"没有时间戳"的真相是机器层有、人读层缺。脚本化维护让漏行/错时刻在机制上不可能 |
| 2026-08-18 | **本文拆分为「概要 + 详设」两份**：实现细节（重跑模型深挖、subprocess/Sheets/增量缓存的动机与代价、认证两段式、全局耦合实测、storage 接口表、tab5 段界、引擎接口）移入 [ARCHITECTURE-DETAIL.md](ARCHITECTURE-DETAIL.md)；本文保留技术栈、架构图、数据流、业务链路、渲染时序、tab 职责、目录说明、数据口径。**同期完成全文事实核查，修正 12 处漂移**：① §1.1 谎称"requirements.txt 声明了 Python 版本下限"（实际从未声明）；② session_state 键数 11→**10**（原文自相矛盾，自己列的名单就是 10 个）；③ 渲染时序大改——侧边栏实为 755–1032（原写 732–993）、tab 声明实为 1033–1043（原写 994–1005）、认证门闸实为 281–583 共 303 行（原写 281–556/327 行，区间与行数不自洽）；④ 六处行号漂移（subprocess 调用、append 读改写、_SHEET_CACHE、增量抓取块等）；⑤ 全局耦合用量表整体重测（八全局按十区分桶，BUG-023/025 术后未重测导致的漂移）；⑥ 引擎行数 930→**938**（原文 §2.1 与 §3.2 自相矛盾）；⑦ .dockerignore 13→16 行；⑧ 根目录表补 CHANGELOG.md、docs 表补 docs/README.md 门户 | 用户发现 §1.1 的 Python 版本错误后要求全文严审。教训：**行号是文档腐坏的头号来源**（12 处错里 9 处是行号/计数），从此立规——行号必须锚点双写 + 节末标复核日期。拆分原则：同一事实只写一处；概要只写稳定事实，顶层变更才更新本文 |
| 2026-08-18 | **BUG-020 刀 2/7：服务层外搬**。新建 `src/context.py`（`Paths`/`Decision`/`build_paths`，启动逻辑原样收编，`code_dir` 按 `parent.parent` 定位——设计文档点名的唯一真实陷阱）+ `src/services/`（model/quotes/curves 三模块，函数显式收 `paths` 参数，零逻辑改动）；app.py 删原 585–754 服务区、5 处调用点改传 `_paths`、imports 精简。**app.py 1559→1381 行**；本文 §3/§4/§5/§6/§7/§9 行号同期平移 | app.py 拆分 7 刀方案第二刀（刀 1 = BUG-025 先行完成）。手术脚本 bottom-up 替换 + 逐行 assert 前置内容；回归：py_compile 全过、引擎 L1 exit 0（15 键齐）、AppTest 冒烟 5 项 PASS、行情缓存备份/还原无污染 |
| 2026-08-18 | **BUG-020 刀 3/7：CSS + 遮罩外搬**。全局 CSS（176 行）搬至 `src/ui/styles.py`（`inject_css()`），三个遮罩组件搬至 `src/ui/overlays.py`；app.py 侧换单行调用 + 同名 import。**app.py 1381→1163 行**；本文 §3/§5/§6/§7/§9 行号同期平移 | 第三刀。遮罩专项核验：`.dca-sync-mask` / `.dca-auth-mask` 的不透明 background 原样随迁（详设 §6 冻屏坑——DOM 在不代表看得见）；AppTest 冒烟 6 项 PASS、行情缓存备份/还原无污染 |
| 2026-08-18 | **BUG-020 刀 4/7：五个只读 tab 外搬**。tab1/2/4/5/6 搬至 `src/tabs/`（today/holdings/history/backtest/strategy_doc，各暴露 `render(tab, ...)` 显式收参）；app.py 侧换 5 行调用，`pandas` 与 `curves` 整行 import 等死引用同步摘除。**app.py 1163→777 行**；本文 §3/§5/§6/§7/§9 改指新模块 | 第四刀。AppTest 冒烟 6 项 PASS（exceptions 0、tab1 metric、tab5 三策略 md、tab6 策略 md、6 tab 齐）、行情缓存备份/还原无污染。tab3 记账是写路径，按方案单独成刀（刀 5） |
| 2026-08-18 | **BUG-020 刀 5/7：tab3 记账写链外搬**。tab3（116 行，写路径：pending_tx/pending_obs → 复述确认 → storage.append_row）搬至 `src/tabs/records.py`（132 行，含文件头注释与类型标注）；app.py 侧换 1 行调用，`json` 死引用同步摘除。**app.py 777→663 行**，六个 tab 全部出主文件；本文 §3/§5/§6/§7/§9 改指新模块 | 第五刀。写链单独成刀的原因是要做**真实写入回归**：local 模式 append_row→read_rows 逐字段断言（tx/obs 各一条，写前备份、验后还原，[OK]×5）；AppTest 冒烟 6 项 PASS（tab3 两表单渲染、exceptions 0）、行情缓存备份/还原无污染 |
| 2026-08-18 | **BUG-020 刀 6/7：侧边栏外搬**。侧栏（278 行：用户管理 + 行情卡片 + 基准金额 + 汇率 + 预算 + 免责声明 + 数据迁移，含模型执行点）搬至 `src/ui/sidebar.py`（304 行，`render(paths, user)` 返回 `Decision` 收口 result/dec/ms/pf）；app.py 侧换 4 行调用，死引用同步摘除（`run_model`/`fetch_*`/`show_loading`/`date` import 与 BASE/TX_CSV/CONFIG 三个过渡桥别名）。**app.py 663→385 行**；本文 §3/§5/§6/§9 改指新模块 | 第六刀。「模型跑两次」病灶（BUG-024）执行点随之入模块（sidebar.py 内 :130/:237）。AppTest 冒烟 9 项 PASS（侧栏标题/行情/汇率/金额+预算输入框/tab1 metric/6 tab 齐/exceptions 0，**含手填 5000 触发金额重跑分支**）、行情缓存备份/还原无污染 |
| 2026-08-18 | **BUG-020 刀 7/7：认证收口，拆分收官**。认证门闸（303 行：登录页渲染 + fail-closed + 三阶段状态机 + 会话首同步）搬至 `src/ui/auth.py`（332 行，`require_user()` 零参数、返回用户名）；app.py 侧换 1 行调用，`contextlib`/`os`/遮罩 import 同步摘除。**app.py 385→78 行，纯装配层，BUG-020 七刀全部落地**；本文 §3/§5/§6/§9 改指新模块 | 第七刀，最高风险隔离单独成刀。两段式防残留设计原样随迁（ph.empty() 真删除 + 遮罩不透明 background，详设 §6）。**AppTest 5 条认证路径 12 项全 PASS**：local 直通 / 凭据缺失 fail-closed / 凭据损坏 fail-closed / 未登录渲染登录页且主界面拦截 / 已登录直通（sheets 状态 monkeypatch 模拟）；行情缓存备份/还原无污染 |
| 2026-08-18 | **BUG-022 修复：新增 `scripts/dca_action.py`（187 行）业务动作 CLI**，Skill 入口经 `record tx` / `record obs` / `override` 子命令与 Web 共用 storage 业务层；shares 可按金额自动换算，sheets 模式写后自动 `sync_local` 刷新落盘缓存 | 此前 Skill 绕过业务层直接调引擎，记账/预算覆盖两边口径分叉；现在写操作全部收口到同一 `storage.py`，双入口行为一致 |
| 2026-08-19 | **BUG-024 修复：引擎行情快照复用 + 侧栏金额表单化**。`dca_calculator.py`（938→983 行）新增 `data/quote_snapshot.json`——抓价成功后落盘 markets 摘要 + 汇率，TTL 600 秒（`--snapshot-ttl` 可调，0 禁用；任一标的抓价失败当趟不落盘），TTL 内运行跳过重复抓价与缓存增量写（下次冷跑自动追平）；输出 JSON 第 16 个顶层键 `quote_snapshot`（used/age_s/ttl_s）。侧栏金额输入收进 `st.sidebar.form("amount_form")`，键入不再逐击触发整页重跑 | 「我想投 X」每次会话此前付两遍完整计算（含 8 个串行行情请求）；实测重跑趟 t2=0.31s 为冷跑 t1=2.72s 的 11.3%（BUGLIST 验收标准 <20%） |
| 2026-08-20 | **BUG-006/007/010/011 修复：抓价链重做**。`dca_calculator.py`（1074→1229 行）抓取层由**串行改并发**（`fetch_history` 用 `ThreadPoolExecutor`，main 把 6 标的 + 2 汇率同波提交，单标的异常收成 `error` 条目不带走整批）+ `fetch_json` 3 次尝试 0.8s/1.6s 退避；落库改由 `save_cached_closes` 三道护栏把关（剔 `date >= utc_today()` 的盘中价 / 行数不减拒写 / temp+`os.replace` 原子写，±20% 跳变只 warning，无变化不碰文件），新增 `utc_today()` 与业务 `biz_today()` 分工；yfinance 兜底统一 `auto_adjust=False` 且明确不落库（落库口径单一由 Chart 路径负责）；新增 `market_freshness` 7 天陈旧闸——超限则 `decision.suggested_amount_rmb=0` + `decision.degraded/freshness`，只展示持仓不出金额（**挂在 decision 内，顶层键仍 18 个**）。UI 侧 sidebar 改按语义判行情正常（不再匹配 `data_source` 前缀）、tab1 加降级横幅 | 8 个行情请求串行、各 20s 超时零重试，最坏 160s 紧贴 subprocess 180s 上限（实测改后 1.5s）；落库是 `open("w")` 整文件截断重写、唯一校验只有 `close > 0`，盘中价直接入库且脏值永久冻结；两条抓取路径复权口径不一致（Chart raw vs yfinance adjusted）；Yahoo 挂三周仍照常出金额，增量抓取 `allow_empty=True` 让空响应静默算成功 |
| 2026-08-20 | **BUG-015 工程安全网落地**：依赖拆成 Cloud/Linux 可安装范围 `requirements.txt` 与 Windows/Python 3.14 精确锁 `requirements-dev.lock`；新增只收 `tests/` 的 pytest 三层离线回归（引擎纯函数 / storage 安全路径 / AppTest 整页冒烟），离线改由 `conftest` 内 autouse 的 `_deny_network` 强制（socket/DNS/子进程四口全拦、回环放行）并由 `test_offline_guard.py` 反向罩住；GitHub Actions 在 push `main` 时分别验证 Windows 3.14 锁定环境与 Linux 3.12 Cloud 范围环境；三个一次性回测脚本去掉失效绝对路径并明确归档、不作回归载体 | 原项目零测试零 CI、依赖既不可复现又可能无界漂移，归档脚本还因旧路径无法运行；现在既验证开发机精确组合，也验证 Cloud 范围仍可安装，且 CI 全离线不受 Yahoo/Sheets 波动干扰。离线单靠各用例自觉 patch 是不可靠的——漏一处或新增抓取路径就静默出网，守卫把这条硬约束变成可执行的 |
| 2026-08-20 | **BUG-028~031 修复：价格存储改「两个落点一条界线」+ 陈旧闸改判实时价**。`dca_calculator.py`（1229→1378 行）：① 新增 `data/market_live.json` 承接当日未收盘 bar（`load/save_market_live` + `split_live_bars` 只收 `date >= utc_today()` + `merge_live_bars` 加载时合并且 **csv 优先**），与 `save_cached_closes` 的剔除闸构成同一条界线的互补两侧——盘中看到的价有持久落点，收盘定稿值落库即自动顶掉临时值，无需清理逻辑；② 新鲜度闸判据从「bar 日期」改为「本次有没有拿到实时价」（新增 `latest_source: "quote" \| "last_close"`，主闸 `!= "quote"` 即降级；`_MAX_STALE_DAYS` 7→10 降级为兜底死标的的副闸）；③ 增量抓取 `period1` 回退 5 天（`_REFETCH_LOOKBACK_DAYS`），请求数不增而数据源的 null 空洞回填与错值修正自动追平。UI 侧 sidebar 新增 `_not_live()`（把 `latest_source` 标记显示出来，缺键保守视为非实时）、tab1 降级文案改取引擎 `freshness.reason` 不写死天数。**顶层键仍 18 个**；`freshness.per_symbol` 形状由 `{sym: int}` 扩为 `{sym: {stale_days, latest_source, quote_time}}` | 旧闸判的是"库里最后一根 K 线多老"，而决策实际用的是 `latest_price`——实测 GC=F 库内 08-18、实时价 08-20，闸放行却没人保证那个价是新的；拿不到实时价时 `latest = closes[-1]` **静默**用旧收盘价冒充，输出无任何标记。落库只收已收盘 K 线后，当日值在项目里**没有任何落点**（盘中记账拿不到自己看到的价）；`close=null` 的空洞与数据源事后修正因"只从库内最后一天往后抓"永久冻结。首跑即验证到真实案例：GC=F `08-13` 被修正 4447.60→4363.60、`08-19` 空洞补上，XAUT `08-13` 修正 + `08-16` 空洞补上 |
| 2026-08-21 | **BUG-017 修复（范围收敛为日志半）：可观测性从"全空"到"出事能查真因"**。新增 `src/obs.py`（62 行）——**只配 handler、不提供 emitter**：`setup_logging()` 幂等、`propagate=False` 不碰 root、stderr + `logs/dca.log`（`RotatingFileHandler` 1 MB × 3）双落点、落盘不可写时降级只留 stderr；`app.py`（66→70 行）在 `storage.init` **之前**调一次。16 个埋点全部落在**已有的**异常/降级分支上，控制流一行没改：`src/ui/auth.py`（328→355）9 处、`storage.py`（612→622）4 处、`src/services/model.py`（45→67）3 处；3 处 `contextlib.suppress(Exception)` 改回 `except Exception as e:` + warning。§11 从"尚未实现"改写为实装 + 三条设计约束 + 两条边界，设计动机与踩坑见详设 §12 | 真正的痛点不是"没打日志"，而是**异常对象被就地销毁**——auth 里 4 处 `except Exception:` 加 3 处 `suppress` 都不留 `e`，事后无法区分"凭据过期""配额撞满""网络抖动"，而三者处置完全不同。同时确认埋点必须在 UI 侧而非引擎：`model.py` 的 `capture_output=True` 会在**成功路径上吞掉引擎 stderr**，埋在引擎里的日志在 Cloud 上根本看不见，而行情降级信息本来就在返回 JSON 的 `degraded` + `freshness` 里。`106 passed`（99→106），五条新断言逐一红检 5/5 全红；外部探针那半拆为 `BUG-034` 判定不修——**零告警是已知边界，不是遗漏** |
| 2026-08-21 | **BUG-032 修复（A 档）：`session_state` 键收进登记表**。新增 `src/state.py`（113 行）——11 个键名常量各标注所属链/生命周期/注意事项 + `ALL_KEYS`（测试断言登记表与实际用键互相覆盖）+ 唯一的 `invalidate_sync()`（收敛"触发重同步"这条跨模块协议）；57 处裸字面量常量化（auth 38 / records 17 / sidebar 2），**键名一字未改、行为零变更**。§9.1 `src/` 行 2011→2137（auth 355→365 / records 182→183 / sidebar 337→339），tests 106→**123**（新增状态键登记表 17 条） | 真实痛点不是"读写次数多"而是**全局可变状态池没有单一记录处**：`session_state` 是 dict-like，写任意键都合法，`synced` 拼成 `synched` 没有任何提示，只会让同步判断永久出错、建议基于陈旧缓存静默出错。不取 B（11 键包 20+ 个一行薄函数、还得复刻 pop/get/in 语义，噪音大于收益）、不取 C（7 个 widget key 与业务键共享同一 dict，dataclass 只能管一半，反造成"看起来管全了"的错觉）。数字两次自查更正：台账原记"60 处"复测为 57 处带键引用（差额=两处文档串散文+一处无键 `clear()`）；确认期写"5 个 widget key"漏数（只扫了 `st.*(key=)`，列对象上的两个没算），实测 7 个，新测试改扫任意 `Call` 的 `key=`。`123 passed`（106→123），六条新断言逐一红检 6/6 全红 |
| 2026-08-21 | **BUG-035 修复（A 档）：弃用参数清零、下界钉死**。7 文件 25 处 `use_container_width=True` 全部换成 `width="stretch"`（官方明示等价物，行为零变更；14 处 dataframe 的新写法恰等于该版本默认值，双保险）；`requirements.txt` streamlit 下界 `1.32.0`→`1.61.1`——`width=` 参数 1.46+ 才有，下界不抬等于把引信接回去；新增 `tests/test_deprecated_api.py` 两断言（AST 零残留带 `stretch>=20` 防呆 + 下界托底正则），tests 123→**125**，红检 2/2 | 弃用截止日 2025-12-31 已过 8 个月，Cloud **每次唤醒都按上界 `<2` 重新解析依赖**（部署日志实证 uv 全量解析 65 包）——上游哪天删掉该参数，下次唤醒即 25 处全 `TypeError`、登录页先崩，引信不在自己手里。发现契机正是 BUG-017 落地当天核对 BUG-032 部署日志：单次渲染 12 条同文弃用警告，观测面第一次抓到真负债 |
| 2026-08-21 | **BUG-018 备份制度落地（Apps Script 每日快照）**。新增 `deploy/backup/Code.gs`（仓内唯一事实源；绑定源表格的容器脚本，每日时间驱动触发 `dailyBackup()`）：四表 CSV + `manifest.json`（行数 + SHA-256）落 Drive `dca-backups/YYYY-MM-DD_HHMMSS/`，超 30 天自动进回收站，失败 `MailApp` 邮件告警（§12 的"零告警"边界在这条每日任务上被补掉）；恢复唯一入口 `restoreSnapshot(dateStr, targetId)` 带"拒绝源表自身"守卫，演练与灾备共用。DEPLOY.md §6 从"缺口"改写为部署 + 演练指引；新增 `tests/test_backup_script.py` 五断言（守源码不变量：表名与 storage 同步含 `TABLES` 字典键间接路径 / 保留 30 天 / 零硬编码 ID / 守卫与告警在场），tests 125→**130**，红检 3/3。**台账状态 🟠**：首次部署与恢复演练只能用户本人在其 Google 账号执行，演练通过前不转 ✅ | 备份三要素（自动调度 / 多时间点 / 演练恢复）原来一条都不满足。弃本机任务计划：本机是公司电脑，可靠性不被信赖，快照含财务记录与 users 表哈希盐不该再堆上去；弃 Actions：仓库公开、artifacts 人人可下，需加密或私有仓，且 GCP 服务账号 key 要再存一份进 Secrets。Apps Script 零新增凭据、零新增信任方，执行与落点都离开公司电脑；代价是备份与源同一 Google 账号，账号级灾难不在防线内（要账号级副本可叠加 Actions→私有仓层，叠加非返工） |
| 2026-08-24 | **引擎拆分：`dca_calculator.py` 1424 行单文件 → 5 个兄弟模块 + 薄入口（240 行）**。按线性依赖 DAG 拆为 `dca_types.py`（211，数据结构/工具/数据加载）→ `dca_market.py`（524，行情抓取/缓存 I/O/汇率）→ `dca_portfolio.py`（131，XIRR/组合计算）→ `dca_scoring.py`（247，评分/决策）→ `dca_table.py`（153，宽表渲染）+ `dca_calculator.py`（240，`main()` + re-export）。无循环引用；所有公共符号通过 re-export 保持 `import dca_calculator as eng` 全部调用方零改动（52 个符号验证全可访问）。同期完成 4 项重构：P1-1 消除数据往返（`build_wide_rows` 结构化中间体替代 markdown→parse 往返）、P1-2 schema DRY（`build_tx_row`/`build_obs_row` 构造函数收口行字典）、P2-1 curves.py 去 sys.path（新建 `src/market_cache.py` 纯函数模块）、P2-3 BTC fallback 带标注（`btc_last.json` + `stale_label`）。`130 passed`，引擎子进程输出验证 5 行×19 列 wide_table_rows | 1424 行单文件导航困难，改一处要全文搜索定位；拆分后按职责分文件，每个模块可独立理解。re-export 策略保证向后兼容：tests 和 dca_action.py 零改动 |

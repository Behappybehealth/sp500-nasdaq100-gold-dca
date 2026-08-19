# 工程架构说明书（概要版）

> 【活文档 · 更新时机：**仅顶层架构变更**（换技术栈/存储/部署、改数据流、调目录、动链路）时更新；实现细节变动只更新详设，不动本文】索引见 [docs/README.md](README.md)。
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
| 版本控制 | git + GitHub 私有仓库 | `Behappybehealth/sp500-nasdaq100-gold-dca` |
| 代码格式 | ruff | 有格式化痕迹，但**没有强制检查**（无 CI） |

（版本复核于 2026-08-18，与本机实装一致）

## 3. 核心架构：三层 + 一个边界

```
                        ┌─────────────────────────────────┐
   浏览器（用户）  ←───→ │  Streamlit Community Cloud      │
                        │  一个 Python 进程服务所有用户     │
                        │                                 │
                        │    app.py（66 行，纯装配层）      │
                        │    认证/侧栏/6 tab 全部在 src/   │
                        └───┬──────────────────┬──────────┘
                            │                  │
              subprocess    │                  │  import
              （子进程）     │                  │
                            ▼                  ▼
        ┌───────────────────────────┐   ┌──────────────────┐
        │ scripts/dca_calculator.py │   │   storage.py     │
        │ 计算引擎（983 行）         │   │  存储层（594 行） │
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

**三层 + 一个边界**：`app.py`（UI + 业务逻辑，耦合较紧）→ 通过 subprocess 隔离 `dca_calculator.py`（纯计算）、通过 import 使用 `storage.py`（数据层）。子进程边界是本项目最干净的设计：改计算不影响 UI，反之亦然。

**拆分已完成**（方案存档见 `docs/plans/app-split-design.md`）：启动路径逻辑收编 `src/context.py`，服务函数（模型调用 / 行情抓取 / 曲线计算）在 `src/services/`，全局 CSS / 遮罩 / 侧栏 / 认证在 `src/ui/`，六个 tab 在 `src/tabs/`；所有模块数据显式收参，不读 app.py 模块级全局。app.py 1559→**66 行纯装配层**（import → build_paths → storage.init → 认证门闸一行 → 侧栏一行 → 6 个 tab render 调用）。

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
① 13–31  import → build_paths()（启动逻辑在 src/context.py）→ storage.init() → set_page_config
② 34–35  注入全局 CSS（inject_css()，样式本体在 src/ui/styles.py）
③ 37–38  认证门闸：CURRENT_USER = auth.require_user()（本体在 src/ui/auth.py；未登录 st.stop()，下面的代码根本不执行）
④ 40–42  侧边栏：sidebar.render(_paths, CURRENT_USER)（本体在 src/ui/sidebar.py），返回 Decision 解包出 result/dec/ms/pf
⑤ 44–54  声明 6 个 tab（st.tabs 在 :45）
⑥ 56–66  渲染 6 个 tab（全部 src/tabs/ 的 render() 调用）
```

**关键点：app.py 已是纯装配层**——每段只剩一行调用 + 指针注释，业务全在 `src/` 对应模块。模型执行点在 `src/ui/sidebar.py` render() 内（首跑 :124、表单提交后金额重跑 :235）；决策结果收口为 `Decision` 返回值，由 app.py 解包显式传给各 tab。

（行号复核于 2026-08-19）

## 7. 六个 Tab 的职责

| tab | 位置 | 行数 | 业务职责 | 依赖 |
|---|---|---:|---|---|
| 🎯 今日模拟 | `src/tabs/today.py` | 93 | 今日建议金额/部署系数/三资产分配/三档执行方案 | render(tab1, result, dec, ms, ASSETS) |
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

### 9.1 根目录

| 路径 | 行数 | 是什么 | 谁读它 | 入库 |
|---|---:|---|---|:---:|
| `app.py` | 66 | Streamlit 主程序，**纯装配层**：import → build_paths → storage.init → 认证一行 → 侧栏一行 → 6 个 tab render 调用 | Streamlit 直接执行 | ✅ |
| `src/` | 1885 | **业务层**（app.py 只留装配）：`context.py`（73，启动上下文 `Paths`/`Decision`/`build_paths`）+ `dates.py`（20，业务"今天"唯一定义 `biz_today()`，Asia/Shanghai 固定 UTC+8，与引擎同规则双实现）+ `services/`（`model.py` 45 模型调用 / `quotes.py` 87 行情抓取 / `curves.py` 102 曲线数据）+ `ui/`（`styles.py` 185 全局 CSS / `overlays.py` 59 三遮罩 / `sidebar.py` 328 侧栏，返回 `Decision` / `auth.py` 328 认证门闸，`require_user()`）+ `tabs/`（`today.py` 93 / `holdings.py` 78 / `records.py` 182 / `history.py` 26 / `backtest.py` 249 / `strategy_doc.py` 18，各暴露 `render(tab, ...)` 显式收参）；不读 app.py 模块级全局 | `app.py` import | ✅ |
| `storage.py` | 612 | 存储层。所有 Google Sheets 读写都走它（含写前快照、PBKDF2 认证、成交同日同资产同方向去重，`force=True` 显式放行）；19 个公开接口明细见详设 §9 | `app.py` import | ✅ |
| `requirements.txt` | 6 | 依赖清单。只约束包版本且几乎全无上界 | Cloud 装依赖时 | ✅ |
| `CHANGELOG.md` | — | **全量改动的人读版流水**：每 commit 一行带 `HH:MM:SS` 时刻（取自 git），由 `scripts/changelog.py` 生成/校验 | 人 | ✅ |
| `start-app.bat` | — | 本机双击启动 | 你 | ✅ |
| `CLAUDE.md` | — | 给 AI 编程助手的项目说明 | AI 助手 | ✅ |
| `README.md` | — | 项目自述 | 人 | ✅ |
| `.dockerignore` | 16 | **Docker 当前未启用**，保留作将来重写 Dockerfile 的安全默认（第一条排除 secrets） | 无（暂时） | ✅ |

### 9.2 `scripts/` — 计算引擎与工具

| 路径 | 行数 | 说明 |
|---|---:|---|
| `scripts/dca_calculator.py` | 1074 | **策略大脑**。完全独立可单跑，不依赖 Streamlit。输入 = CSV + config，输出 = JSON（18 个顶层键）；业务"今天"走 `biz_today()`（与 `src/dates.py` 同规则双实现），坏日期行剔除并输出 `invalid_transactions`；汇率唯一实时源、失败回落 `fx_last.json` 上次成功值（`fx` 三件套标 live/as_of，全无可估值置空）；行情快照 `data/quote_snapshot.json`（TTL 600 秒）复用抓价结果；参数与键明细见详设 §11 |
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
| `data/*.localbak` | 轮转留底文件 | ❌ | 带时间戳的真轮转（覆盖前自动留底 10 份） |
| `data/market_history/*.csv` | **行情缓存**，6 个文件，两列（`date,close`） | ✅ | **入库是刻意的** —— 让 Cloud 部署不用冷启动重抓十年数据 |

**行情缓存 6 个文件**：`_GSPC.csv`（标普指数）、`SPY.csv`（标普 ETF）、`_NDX.csv`（纳指）、`QQQ.csv`（纳指 ETF）、`GC_F.csv`（黄金期货）、`XAUT_USD.csv`（黄金代币）。孤儿 `GLD.csv`（不在抓取名单、冻结在 2026-08-10）已从仓库移除，取回：`git show f1ed967:data/market_history/GLD.csv`。

### 9.4 `backtest/` — 回测（一次性产物，非运行时依赖）

| 路径 | 说明 |
|---|---|
| `backtest_dca.py` / `backtest_single.py` / `backtest_compare3.py` | 回测脚本。⚠️ 全部写死旧绝对路径 `C:\Users\xiezhibo\.claude\skills\...`，现在跑不起来 |
| `results_compare3.json` | Tab5 第一段读它（三策略对比） |
| `results_single_compare.json` | Tab5 第三段读它（单品种动态 vs 固定） |
| `results_rolling.json` | Tab5 第三段四张滚动表 + 第四段横向对比读它（从 app.py 硬编码无损导出，33 行 × 338 格与原字面量逐格相等） |
| `results.json` / `results_single.json` / `results.md` / `compare3.md` | 中间产物与文字报告，应用不读 |

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

| 路径 | 说明 | 入库 |
|---|---|:---:|
| `docs/README.md` | **文档门户**：全部说明文件的索引（活/冻标注、读者、更新时机） | ✅ |
| `docs/ARCHITECTURE.md` | **本文件**。顶层架构唯一事实源 | ✅ |
| `docs/ARCHITECTURE-DETAIL.md` | **架构详设**：实现细节、设计动机、踩坑记录 | ✅ |
| `docs/BUGLIST.md` | **问题台账**。每条走「梳理 → 1对1确认 → 修复 → 验证」四段 | ✅ |
| `docs/plans/` | 计划与历史审计存档（`app-split-design.md` = app.py 拆分 6 刀方案；`project-audit-2026-08-17.md` = 原始审计快照） | ✅ |
| `.streamlit/config.toml` | 主题配色 | ✅ |
| `.streamlit/secrets.toml` | **GCP 服务账号凭据**，2600 字节 | ❌ |
| `.streamlit/secrets.toml.example` | 模板 | ✅ |

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
| 运行日志 | 应用运行时事件（登录、同步、抓取）——**尚未实现** | `logs/`（约定落点；`*.log` 不入库，`.gitkeep` 占位） | ❌ |

⚠️ `logs/` 落点只对**本机 / 长期部署**有意义：Streamlit Cloud 容器重启即丢文件系统，届时运行日志需另配持久化（那是运行日志实现时要解的题）。

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

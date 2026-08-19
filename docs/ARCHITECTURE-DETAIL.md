# 架构详设（实现细节与设计动机）

> 【活文档 · 更新时机：行为变更同期更新】索引见 [docs/README.md](README.md)。
> **分工**：顶层架构（技术栈、架构图、数据流、业务链路、tab 职责、目录、数据口径）的唯一事实源是 [ARCHITECTURE.md](ARCHITECTURE.md)；本文装**实现层的细节、设计动机、代价与踩坑**。同一个事实只写在一个地方，另一处只引用。
> **变更历史不在本文重复**——每个 commit 见根目录 [CHANGELOG.md](../CHANGELOG.md)；顶层变更另记 ARCHITECTURE.md 变更记录。
> **行号约定**：锚点 + 行号双写（代码动了先按锚点找，再修行号）；每个含行号的节末尾标复核日期。问题与缺陷走 [BUGLIST.md](BUGLIST.md)。

---

## 1. Streamlit 的"脚本重跑"模型

这一点必须先弄懂，因为后面**至少三个严重问题都源于它**。

传统网页框架（Flask/Django）是这样：你写一堆"函数"，用户访问哪个网址就调用哪个函数。函数之间互不干扰。

**Streamlit 完全不同：**

```
用户第一次打开页面
   → 服务器从第 1 行开始，把 app.py 整个文件执行到最后一行
   → 执行过程中每遇到 st.xxx() 就往页面上画一个东西

用户点了一下按钮/滑块/输入框
   → 服务器又从第 1 行开始，把 app.py 整个文件重新执行一遍
   → 重画整个页面
```

**打个比方**：像一个厨师，你每换一次口味要求，他不是只改那一步，而是**把整本菜谱从第一页重念一遍**，只是这次在某一页做了不同的选择。

这个模型带来三个必然的后果，Streamlit 用三个机制来应付：

| 问题 | Streamlit 的机制 | 项目里怎么用的 |
|---|---|---|
| 重跑一遍，之前用户填的东西不就丢了？ | **`st.session_state`** —— 一个字典，跨"重跑"活着，每个浏览器标签页各有一份 | 存登录状态 `user`、待确认的记账 `pending_tx` 等 **10 个键**（明细见 §8） |
| 重跑一遍，慢操作（抓行情、算模型）每次都重做？ | **`st.cache_data`** —— 记住函数的结果，下次同样参数直接返回 | `run_model()` 缓存 900 秒，缓存键含用户名（多用户不串号） |
| 重跑一遍，怎么让页面停在登录页不往下走？ | **`st.stop()`** —— 立刻停止本次执行 | 认证门闸用它拦住未登录用户 |

> **最关键的一条：`st.session_state` 是"每个浏览器一份"，但 `st.cache_data` 是"整个服务器共用一份"。**
> 这个区别很容易搞混——所以 `run_model()` 的缓存键必须含用户身份，否则 A 用户的结果会被 B 直接命中。

**还有一个必须知道的怪癖**：以 `st.rerun()` 结束的运行**不会清除该趟未重新渲染的旧元素**。所以瞬态页面（比如登录页）必须整体挂进一个固定的 `st.empty()` 容器，跳转前用 `容器.empty()` 把它从 DOM 里**真删除**，而不是用遮罩盖住 —— 否则登录表单会残留并漂在主应用上。这个坑踩过三轮，细节见 §6。

---

## 2. 为什么计算用"子进程"（subprocess）

`app.py` 自己不算策略。它是这样做的：

```
app.py  ──启动一个全新的 Python 程序──→  scripts/dca_calculator.py
        ←──── 对方把结果打印成 JSON 文字 ────
        （app.py 读这段文字，转成数据用）
```

用代码看就是 `run_model()` 内（src/services/model.py:19 定义，subprocess 调用在 :31）：

```python
cmd = [sys.executable, "scripts/dca_calculator.py", "--base-dir", str(BASE)]
out = subprocess.run(cmd, capture_output=True, timeout=180)
return json.loads(out.stdout)          # 把对方打印的 JSON 转成 Python 字典
```

**打个比方**：不自己做饭，打电话叫外卖。好处是厨房（引擎）和餐厅（UI）完全隔离——改菜谱不会弄坏餐厅装修。**这个设计是对的，也是这个项目最干净的一处。**

代价是：两边只能通过"命令行参数"和"打印出来的文字"沟通。**引擎不知道谁是当前用户**，它只知道 `--base-dir` 指向哪个目录、那个目录里的 CSV 写了什么——所以多用户模式靠 `--user` 参数让引擎改读 `data/users/<user>/` 下的分目录数据。

（行号复核于 2026-08-19）

---

## 3. 为什么用 Google Sheets 当数据库，代价是什么

好处很实在：不用买服务器、不用装数据库、你能用手机打开表格直接看数据、免费。

代价是**它没有"改一行"的能力**。真正的数据库你可以说"把第 5 行的金额改成 1000"。Google Sheets 这套库只能：

- 读：把**整张表**拉下来
- 写：把**整张表**推上去（覆盖）

所以"加一笔成交记录"的实际动作是（`append_row()` 的 Sheets 分支，storage.py:444–446）：

```
把整张 transactions 表读下来（比如 200 行）
  → 在末尾拼上你这 1 行（变 201 行）
  → 把这 201 行整体推上去，覆盖原来的 200 行
```

**打个比方**：改一个字，要把整本书重新抄一遍再交上去。**所以"读下来"这一步是命门**——如果它失败了，你手里就是一本空书，抄完交上去，原来那本就没了。storage 因此定下两条规矩：读失败抛错拒写（空表不覆写）、写前先把现内容快照到 `_bak` 表。

另有**进程内全局 8 秒短缓存** `_SHEET_CACHE`（storage.py:138–139，TTL 定义在 :139），`_read_ws(..., fresh=True)`（:149 起）可绕过它强制新鲜读。

四张主表的字段结构见概要版 §8（数据在哪），不重复。

（行号复核于 2026-08-18）

---

## 4. 行情缓存的"增量"机制与降级链

十年历史行情不可能每次都重抓。所以缓存是这样的（`data/market_history/*.csv`，两列 `date,close`）：

```python
dca_calculator.py:313-316   if cached: last_cached = ...; fetch_chart(period1=last_cached, ...)  # 从缓存最后一天开始抓
dca_calculator.py:293-296   save_cached_closes() 内 path.open("w") 整文件截断重写 + 排序
```

`period1 = last_cached` 意味着**最前沿那一天每次运行都会被重抓并按日期键覆盖**。

- **好处**：当天的价格会自动从盘中价修正成收盘价（自愈一天）
- **代价**：只要有更晚的日期落库，前一天就再也不会被重新请求 —— **脏值永久冻结**（这也是每次跑引擎/AppTest 前要备份行情缓存、跑后还原比对的原因）

**抓取路径与降级顺序**：

```
Yahoo Chart v8（urllib 直连，20s 超时，0 次重试）
   ↓ 失败且有缓存
标 data_source="cache_stale" + warning（不再尝试 yfinance）
   ↓ 失败且无缓存
yfinance 兜底（auto_adjust=True）

东财 push2（curl 子进程，3 次重试无退避）→ XAU / BTC 实时价
```

⚠️ 两条路径**复权口径不一致**：Chart 走原始 `quote.close`，yfinance 兜底用 `auto_adjust=True`。

（行号复核于 2026-08-19）

---

## 5. 依赖声明与版本漂移

`requirements.txt` 全 6 行，只约束包版本，且几乎全是无上界的 `>=`：

```
streamlit>=1.32.0        本机实装 1.61.1
yfinance>=0.2.40         本机实装 1.6.0
pandas>=2.0.0            本机实装 3.0.5   ← 跨了一个大版本
numpy>=1.24.0            本机实装 2.5.2
st-gsheets-connection>=0.1.0   本机实装 0.1.0
gspread>=5.8.0,<6        本机实装 5.12.4  ← 唯一有上界的
```

无 lock 文件。**声明的和实装的差很远，等于没有可复现性** —— 上游一发版就可能把线上打挂；复刻环境时以 CLAUDE.md「技术栈」节的实装版本为准。

（复核于 2026-08-19）

---

## 6. 认证链深挖（src/ui/auth.py，328 行）

> app.py 侧仅剩 :38 一行 `CURRENT_USER = auth.require_user()`，本节锚点全部在 auth.py。

三阶段状态机，全部走 `st.session_state`：

| 阶段 | 触发 | 做什么 |
|---|---|---|
| `login` | 默认 | 名字 + PIN 校验 → `storage.authenticate()` |
| `activate` | 账号存在但未激活 | 首次设 PIN → `storage.set_pin()` |
| `bootstrap` | users 表为空 | 首个注册者自动成为 admin → `storage.create_user()` |

区间构成：登录/激活/自举页渲染函数 `_render_login_page()` 定义于 auth.py:20；门闸入口 `require_user()` 在 :168（零参数，全部输入走 storage / session_state / 环境变量）；fail-closed 认证模式判断在 :174 一带（`DCA_AUTH_MODE=local` 才进单机模式，secrets 缺失/损坏 :191 `st.stop()`）；登录页渲染前的名单读取同样 fail-closed（:305–313，读不出名单直接报错停住，绝不渲染"创建管理员"表单）；登录门闸执行在 :315–317（`with _login_ph.container(): _render_login_page(...)` + `st.stop()`）；:318–327 是登录成功后的**会话首同步**（`storage.sync_local(user)`，同步失败不阻塞但给可见警告）；:328 返回用户名。

**两段式设计（踩过 3 轮坑，不要动）：** 点击那一趟**零网络 I/O**（用户名单取 session 缓存），把意图写进 `session_state["_auth"]` → `ph.empty()` 把登录页从 DOM 里**真删除**（不是遮住）→ 挂 `show_auth_mask` → `st.rerun()`。下一趟才在遮罩后面做全部网络工作。

**两个具体的坑**：

1. 以 `st.rerun()` 结束的运行不会清除该趟未重新渲染的旧元素 → 登录表单残留并漂在主应用上
2. 遮罩必须写 `background` —— 曾因遮罩透明导致残留登录页透出，**DOM 检查全过但用户看到冻屏**。验证遮罩不能只看元素在不在 DOM，要查 computedStyle 背景不透明度或截图

（行号复核于 2026-08-19）

---

## 7. 决策链：两次模型运行与行情快照

```
侧栏渲染 ──→ run_model(None) ──subprocess──→ scripts/dca_calculator.py ──→ JSON
                    │
                    └─→ result / dec / ms / pf 收口为 Decision 返回值
                                │
              ┌─────────────────┼──────────────────┐
            tab1 今日模拟      tab2 持仓曲线      tab3 记账
         (result×9 dec×6 ms×4)  (pf×8)        (result×3 dec×3)
```

服务函数在 `src/services/`（`run_model` model.py:19、`parse_wide_table` model.py:42、`fetch_xau_spot` quotes.py:18、`fetch_btc` quotes.py:64、`_load_json` curves.py:19、`load_price_series` curves.py:28、`portfolio_curve` curves.py:44；全部显式收 `paths: Paths` 参数，`Paths` 定义于 `src/context.py`）；执行点在 `src/ui/sidebar.py` render() 内（首跑 :124、表单提交后金额重跑 :235），app.py 侧仅剩 :41 一行调用，结果收口为 `Decision` 返回值（`src/context.py`）后解包。下游 tab1/tab2/tab3 分别在 `src/tabs/today.py` / `holdings.py` / `records.py`（result/dec/ms/pf 等数据全部由 app.py 以显式参数传入 render()）。

**模型会跑两次，但第二遍不再白跑**：侧栏先 `run_model(None)` 自动定额；用户在 `amount_form` 表单里提交金额后再 `run_model(amount_in)` 整体重跑一遍子进程——重跑趟命中引擎的行情快照（`data/quote_snapshot.json`，TTL 600 秒，`--snapshot-ttl` 可调，0 禁用；任一标的抓价失败当趟不落盘），跳过 8 个串行行情请求与缓存增量写（下次冷跑自动追平），实测第二趟耗时约为首趟的 11%。引擎输出第 16 个顶层键 `quote_snapshot`（used/age_s/ttl_s）自报快照命中情况。

（行号复核于 2026-08-19）

---

## 8. 全局耦合实测清单

> ⚠️ **拆分前基线**（1559 行版 app.py，2026-08-18 上午实测）。app.py 现已收口为 66 行纯装配层——本表保留为历史基线不再重测。收口后 app.py 模块级全局仅剩 6 个（`_paths` / `CODE_DIR` / `DATA_DIR` / `ASSETS` / `BACKTEST_DIR` / `CURRENT_USER`），全部是装配参数，业务代码零引用模块级全局。
>
> 口径：`\b名字\b` 在 app.py 的出现次数（含注释提及），按结构区分桶。分区边界见概要版 §6（渲染时序）。2026-08-18 重测（上一版数字在死代码清理与 Tab5 数据导出两次改动后已漂移，该次全部重算）。

**模块级全局 8 个**，作用域比想象的窄得多：

| 全局 | 总用量 | 顶层 | 认证 | 服务 | 侧栏 | tab1 | tab2 | tab3 | tab4 | tab5 | tab6 | 归属判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `CURRENT_USER` | 17 | 0 | 3 | 0 | 10 | 0 | 0 | 2 | 2 | 0 | 0 | 会话态 |
| `DATA_DIR` | 8 | 5 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `ASSETS` | 8 | 1 | 0 | 0 | 0 | 1 | 1 | 5 | 0 | 0 | 0 | 配置 |
| `CODE_DIR` | 6 | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 服务层 + tab6（tab6 用它定位 strategy/） |
| `BASE` | 4 | 3 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `CONFIG` | 3 | 2 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 配置 |
| `TX_CSV` | 4 | 1 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `BACKTEST_DIR` | 4 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | **只 tab5 用** |

`DATA_DIR / BASE / TX_CSV` 在**任何 UI 代码里都是 0 次** —— 它们只被服务函数和顶层初始化用。这意味着 UI 层根本不需要看见路径。

**session_state key 10 个**（口径：[`key`] 读写 + `.pop()` / `.get()` 调用合计）：`_auth`(8) `synced`(7) `_login_err`(6) `_names`(5) `activating`(5) `pending_tx`(5) `user`(4) `pending_obs`(4) `_boot_err`(2) `_act_err`(2)，另有 1 处 `.clear()`。其中 8 个属认证/会话链、2 个属记账链（`pending_tx` / `pending_obs`），**没有跨链共享**。

**storage 接口 19 个公开函数**（app.py 侧调用次数）：`sync_local`×4、`read_rows`×2、`list_users`×2、`is_admin`×2、`append_row`×2、`sheets_status`×2，其余 12 个各 ×1，`sheets_enabled` 仅 storage 内部使用 —— 已是干净边界。

（复核于 2026-08-18）

---

## 9. storage.py 接口表（19 个公开函数）

| 函数 | 行号 | 职责 |
|---|---:|---|
| `sheets_status` | 96 | 云端可用性状态（fail-closed 判定用） |
| `sheets_enabled` | 108 | 是否已配凭据（内部用） |
| `list_users` / `list_users_fresh` | 250 / 256 | 用户名单（后者绕过 8 秒缓存） |
| `authenticate` | 263 | 一次新鲜读完成「锁定/存在性/激活态/PIN」四重判断；连续失败 5 次锁 15 分钟；旧 sha256 账号验证通过即自动迁移 PBKDF2 |
| `create_user` | 314 | 注册（首个用户自动 admin） |
| `is_admin` / `admin_add_user` | 342 / 348 | 管理员工具 |
| `is_activated` / `set_pin` | 370 / 377 | 激活态 / 设 PIN（新 PIN 强制 6–8 位） |
| `delete_user` / `reset_pin` | 398 / 407 | 删号 / 重置 PIN |
| `read_rows` / `append_row` | 425 / 439 | 按用户读行 / 追加行（Sheets 分支是整表读改写，见 §3） |
| `get_overrides` / `set_override` | 459 / 479 | 月度预算覆盖 |
| `sync_local` | 519 | 云端数据落盘到 `data/users/<user>/`（覆盖前带时间戳轮转留底 10 份） |
| `import_local_to_sheets` | 543 | 本地历史一次性上传云端 |
| `init` | 583 | 启动初始化（数据目录定位） |

认证相关常数：`_PBKDF2_ITER = 200_000`（:87，2026-08-17 本机实测约 0.073s/次；换部署机应按 0.05–0.3s 目标重新标定）。

（行号复核于 2026-08-18）

---

## 10. tab5「回测结果」内部构成（src/tabs/backtest.py，249 行）

> 本 tab 在 `src/tabs/backtest.py`（`render(tab, backtest_dir)` :17）；段界对齐模块内 `# ========== ①…⑤` 注释锚点。

| 段 | 行区间 | 行数 | 数据来源 |
|---|---|---:|---|
| 一、三策略对比 | 24–108 | 85 | ✅ 读 `results_compare3.json` |
| 二、为什么定额等比最高 | 109–121 | 13 | 纯 markdown |
| 三、单品种滚动回测（含四张子表） | 122–213 | 92 | ✅ 读 `results_single_compare.json` + `results_rolling.json` |
| 四、四标的横向对比 | 214–219 | 6 | ✅ 读 `results_rolling.json` |
| 五、综合结论 | 220–249 | 30 | 纯 markdown |

全 tab 统一从文件读数；文件缺失时 warning 优雅降级（加载器 `_load_json`，src/services/curves.py:19）。

（行号复核于 2026-08-18）

---

## 11. 计算引擎接口（scripts/dca_calculator.py，983 行）

**参数五个**：`--amount`、`--base-dir`、`--history-years`、`--user`（多用户模式：记账数据从 `data/users/<user>/` 读取，config 与行情缓存保持共享；含路径穿越防护）、`--snapshot-ttl`（行情快照 TTL 秒数，默认 600，0=禁用。没有 `--no-refresh`）。

**输入文件**：`data/config.json`、`data/market_history/`、`data/quote_snapshot.json`（存在且未过期时免抓价）、记账三件套——无 `--user` 时读 `data/{transactions,observations}.csv` + `data/budget_overrides.json`，有 `--user` 时读 `data/users/<user>/` 下同名文件。

**行情快照**：`load_quote_snapshot()`（:407）/ `save_quote_snapshot()`（:425），落盘 `data/quote_snapshot.json`（`fetched_at` / markets 摘要 / `usdcny` / `usdtusd`，仅几 KB）。TTL 内命中（:885）则跳过行情抓取与缓存增量写——下次冷跑自动追平，不丢历史；任一标的抓价失败（带 `error`）当趟不落盘（:896 一带），防止坏快照连环命中。快照只是加速缓存：过期自动失效、缺失自动全抓，不入库。

**输出**：print 一大段 JSON（:979），**16 个顶层键**——字面量构造 15 个（:961–976：`as_of` / `input_amount_rmb` / `effective_amount_rmb` / `usdcny` / `usdtcny` / `monthly_budget_status` / `config` / `has_local_transactions` / `last_records` / `since_last_record` / `markets` / `quote_snapshot` / `portfolio` / `decision` / `suggested_weights`），随后 :978 追加 `wide_table_markdown`（由 `render_wide_table()` :753 生成，app.py 侧 `parse_wide_table()` 解析回 DataFrame，src/services/model.py:42）。

单独跑它：

```bash
.venv/Scripts/python.exe scripts/dca_calculator.py --base-dir .
.venv/Scripts/python.exe scripts/dca_calculator.py --amount 5000 --base-dir .
```

（行号复核于 2026-08-19）

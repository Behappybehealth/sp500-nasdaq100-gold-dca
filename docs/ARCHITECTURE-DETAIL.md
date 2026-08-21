# 架构详设（实现细节与设计动机）

> 【活文档 · 更新时机：行为变更同期更新】索引见 [docs/README.md](README.md)。
> **分工**：顶层架构（技术栈、架构图、数据流、业务链路、tab 职责、目录、数据口径）的唯一事实源是 [ARCHITECTURE.md](ARCHITECTURE.md)；本文装**实现层的细节、设计动机、代价与踩坑**。同一个事实只写在一个地方，另一处只引用。
> **变更历史不在本文重复**——每个 commit 见根目录 [CHANGELOG.md](../CHANGELOG.md)；顶层变更另记 ARCHITECTURE.md 变更记录。
> **行号约定**：锚点 + 行号双写（代码动了先按锚点找，再修行号）；每个含行号的节末尾标复核日期。**本文是全项目唯一维护"当前行号"的地方** —— [BUGLIST.md](BUGLIST.md) 里的 `file:line` 是冻结的历史坐标（记录当时的现场），不要回头去改它。问题与缺陷走 [BUGLIST.md](BUGLIST.md)。

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

用代码看就是 `run_model()` 内（src/services/model.py:24 定义，subprocess 调用在 :36）：

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

所以"加一笔成交记录"的实际动作是（`append_row()` 的 Sheets 分支，storage.py:461–465）：

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
dca_calculator.py:503-509   if cached: last_cached = ...; fetch_chart(period1=last_cached - 5 天, ...)  # 从缓存最后一天回退 5 天开始抓
dca_calculator.py:344-386   save_cached_closes() 三道护栏 + temp/os.replace 原子替换
dca_calculator.py:389-462   market_live.json 读写 + split/merge（当日未收盘 bar 的另一个落点）
```

`period1 = last_cached - _REFETCH_LOOKBACK_DAYS`（5 天）意味着**最近 5 天每次运行都会被重抓并按日期键覆盖**。

- **好处**：① 当天的价格自动从盘中价修正成收盘价；② 数据源事后回填的 `close=null` 空洞会被补上；③ 数据源事后修正的错值会被追平。②③ 是刻意的——首次上线当天就抓到真实案例：GC=F `2026-08-13` 被数据源从 4447.60 修正到 4363.60、`08-19` 的空洞补上（详见 BUG-030/031）
- **代价**：① 5 天窗口之外的存量差异仍然冻结（接受为既有事实，不做全量重抓）；② 最近 5 天的值每次跑都可能因数据源的浮点表示微差被改写（实测 `_NDX` `08-19` 29426.023438→29426.019531，差 4e-6），于是 `git status` 常有无意义 diff——这是拿"自动追平"换来的，别当成 bug
- ⚠️ 每次跑引擎/AppTest 前仍要备份行情缓存、跑后比对：护栏拦得住残缺，拦不住"数据源整段给错"

**落库三道护栏**（`save_cached_closes` 返回 warning 列表，调用方透传到 JSON 的 `persist_warnings`）：

| 闸 | 规则 | 为什么 |
|---|---|---|
| ① 冷热分离 | 剔除 `bar_date >= utc_today()` | 盘中价不进库。UTC 午夜是美股（16:00 ET = UTC 20/21:00）、GC=F、24/7 的 XAUT 都已走完前一 UTC 日的**统一安全线**，一条规则覆盖全部标的，不必按标的维护收盘时刻表 |
| ② 行数不减 | `len(新) < len(库内)` 拒写 | 上游返回残缺数据时不把库削平 |
| ③ 原子写 | `<name>.tmp<pid>` + `os.replace` | 写盘中途挂掉不留残缺文件；`OSError` 时清 tmp 并挂 warning |

配套两条：**±20% 跳变只报 warning 不拦**（1987 式真崩盘必须能落库），且只查本次真正变更的日期，否则历史老跳变每次跑都刷一遍；**`persistable == existing` 时不碰文件**（盘中/周末重跑是常态，不该产生 mtime 抖动）。

⚠️ **`utc_today()`（落库安全线）与 `biz_today()`（业务日界，UTC+8）是两个函数两个用途**，别互相替换。K 线日期也按 UTC 解释（`datetime.fromtimestamp(ts, timezone.utc).date()`）——用本机时区会让 XAUT 的 UTC 00:00 bar 在负偏移时区整体错位一天，同一份数据在不同机器上标不同日期。

**两个落点、一条界线**：护栏①把当日值挡在 csv 之外，那"我盘中看到的那个价"就必须另有落点，否则盘中记账时它在项目里根本不存在。

| 落点 | 收什么 | 谁写 | 入库 |
|---|---|---|---|
| `data/market_history/*.csv` | 已收盘定稿收盘价（`date < utc_today()`） | `save_cached_closes`（剔除当日） | ✅ |
| `data/market_live.json` | 当日未收盘 bar（`date >= utc_today()`） | `save_market_live`（`split_live_bars` 只留当日） | ❌ |

两个函数用的是**同一条界线的互补两侧**，所以既不会重复也不会漏。加载时 `merge_live_bars(csv, live)` 合并成一条序列，**csv 优先**（`setdefault`）——定稿值一落库就自动顶掉临时值，不需要任何清理逻辑；反过来写会让盘中价每次盖掉定稿价。

两个必须守住的细节：

- **当日 bar 的日期取自数据源自己的 bar 时间戳**，不按 `regularMarketTime` 自行推算交易日归属。休市标的根本不会开出当日 bar → 自然没有当日点、也不会和上一收盘撞成重复点；24/7 标的自然有。自造时区归属规则是错位的温床
- **`fresh_live is None` 表示"本次没拿到当日 bar 的可信信息，别动存量"**（兜底路径与 `error` 条目都是 None）。若此时照写存量，就会给陈旧值盖上新的 `fetched_at`——把旧值伪装成刚抓的，比不写危险得多。写只发生一次：`fetch_history` 收齐全部线程后统一做（`market_live.json` 是全标的共用文件，放线程里写会互相覆盖）

**抓取路径与降级顺序**：

```
Yahoo Chart v8（urllib 直连，20s 超时 × 3 次尝试，0.8s/1.6s 退避）
   ↓ 失败且有缓存
yfinance 兜底（auto_adjust=False，结果只进内存不落库）→ data_source="yfinance_fallback+cache"
   ↓ 也失败
标 data_source="cache_stale" + cache_warning（吃旧缓存，且必然被实时价主闸拦下）
   ↓ 失败且无缓存
yfinance 兜底（同样不落库）→ "yfinance_full_no_cache"；再失败才返回 error

东财 push2（curl 子进程，3 次重试无退避）→ XAU / BTC 实时价
```

两条路径**复权口径已统一为 raw**（Chart 取原始 `quote.close`，yfinance 兜底 `auto_adjust=False`）。落库**只**发生在两个 Chart 成功分支——`save_cached_closes(cache_path, cached)` 全文件恰好 2 次，这是可断言的不变量：兜底数据在物理上进不了库，所以口径切换无需重建缓存。

**两个 warning 键语义不同，别混用**：

- `cache_warning` = 数据没更新到最新 → sidebar 标"部分异常"、宽表 `asset_note` 写"缓存未更新"
- `persist_warnings` = 落库护栏说了话（拒写/跳变/落库失败），**与新鲜度无关**

sidebar 判"行情全部正常"按语义（`src/ui/sidebar.py` 的 `_not_live()`：`cache_warning` 或 `latest_source != "quote"` 即视为非实时，缺键也保守算非实时）而非 `data_source` 前缀——前缀匹配一加新降级路径就会静默漏判。引擎标了降级而界面不显示，等于没降级。

**新鲜度闸主副两道**（`market_freshness`，:969）：判据是**决策实际用的那个价新不新**，不是"库里最后一根 K 线多老"。

| 闸 | 判据 | 拦什么 |
|---|---|---|
| 主闸 | `latest_source != "quote"` | 本次没拿到 `regularMarketPrice`，`latest` 回落成最后一根收盘价 |
| 副闸 | `history_end` 距 `biz_today()` > `_MAX_STALE_DAYS`（10 天） | 数据源长期返 quote 但 K 线早已停更的死标的 |

三个信号标的（`^GSPC` / `^NDX` / `GC=F`）任一被拦或带 `error` → `decision.suggested_amount_rmb = 0`、`level_label = "行情不可用·暂停出金额"`，只展示持仓不出金额（旧价算出的"今天买多少"比不给建议更危险）。挂载在 `decision.degraded` + `decision.freshness` 内，**不新增顶层键**。

- **主闸是本闸的重点**：拿不到实时价时 `latest = closes[-1]` 会**静默**用旧收盘价冒充实时价。新增 `latest_source: "quote" | "last_close"` 把这件事显式标出来，与 `fx.{live,as_of}` 的三态语义对齐——降级不可见等于没降级
- **休市不算不新鲜**：实时价等于上一根收盘价照样是 `quote`（数据源确实回了当前报价），闸放行。用户的"不能使用昨天的值"落点是"禁止在拿不到实时价时用旧值冒充"，不是禁止用昨天的收盘价
- **副闸 7→10 天且语义降级**：旧值 7 天是主判据，现在只是兜底，放宽减少误拦。边界是 `>` 而非 `>=`，落后正好 10 天不拦
- **yfinance 兜底一律 `last_close`**：兜底只给收盘序列、不给实时报价，按拿不到实时价处理 → 被主闸拦
- `freshness.per_symbol` 形状是 `{sym: {stale_days, latest_source, quote_time}}`（旧形状 `{sym: int|None}`，引擎外无消费者）。`quote_time` 只记录展示、不设闸——死标的场景已由副闸覆盖，再加一道时间闸会误拦长假

（行号复核于 2026-08-20）

---

## 5. 依赖声明、精确复现与 Cloud 可安装性

依赖刻意分成两份，因为「复刻 Windows/Python 3.14 开发机」与「让 Streamlit Cloud 的 Linux/较低 Python 装得上」不是同一个目标：

| 文件 | 约束 | 消费方 | 保证什么 |
|---|---|---|---|
| `requirements.txt` | 6 个直接依赖的范围；每项都有下界和大版本上界 | Streamlit Cloud + CI Linux/Python 3.12 腿 | Cloud 平台可解析出兼容 wheel，同时阻止未来大版本无界漂移 |
| `requirements-dev.lock` | Windows/Python 3.14.4 的完整 `pip freeze`，直接/间接依赖全部 `==` | 本机开发 + CI Windows/Python 3.14 腿 | 精确复刻已验证开发组合 |

Cloud 不装 lock：里面含 Windows 平台包，而且本机钉定版本未必为 Cloud 的 Python/Linux 提供 wheel。反过来，开发复现不能只装范围文件，否则今天和下周可能解析到不同的间接依赖。因此 CI 跑两条互补腿：Windows 3.14 安装 lock 验证精确组合，Linux 3.12 安装范围文件验证实际部署入口仍能解析；后者单独安装同版本 pytest（测试工具不是 Cloud 运行时依赖）。两条测试都完全离线运行，安装依赖本身当然仍需访问包仓库。

变更 Python 或平台时的重建步骤：按 `requirements.txt` 新建干净 venv → 安装并跑全量测试 → 用该环境的 `python -m pip freeze` 覆盖 `requirements-dev.lock` → 再跑一次全量测试。不能在另一平台手工删改几行后仍称其为同一份精确 lock。

（复核于 2026-08-20）

### 5.1 离线测试边界

`pytest.ini` 把收集范围固定为 `tests/`，避免 `backtest/*.py` 这类顶层即执行的一次性脚本被 pytest 误跑。测试分三层：

1. `test_engine.py` 直接 import 独立引擎脚本，给纯函数固定输入并断言确定输出；不调用 `main()`，因此不抓行情、不写缓存。
2. `test_storage.py` 分本地临时目录与内存假 Sheets 两路；每条路径都显式 patch `storage.sheets_enabled`。这不是多余防御：仓库本机存在真实 `.streamlit/secrets.toml`，若依赖“测试机通常没 secrets”，pytest 可能直接写生产表。
3. `test_smoke.py` 用 `streamlit.testing.v1.AppTest` 完整执行 app 六个 tab，但 patch `src.ui.sidebar.run_model` 返回虚构结果、patch XAU/BTC 报价服务。虚构行情与汇率仍流过真实引擎纯函数，日期一律相对 `biz_today()` 生成；AST 再从 `dca_calculator.py` 的最终 result 字面量抽取顶层键，防 fixture 契约悄悄漂移。

这条整页冒烟有明确边界：它证明“认证 local 路径、侧栏和六个 tab 能消费一份合法引擎 JSON”，**不证明** UI→subprocess→stdout JSON 的真实接线，也不证明外部行情 API 可用。后两者仍由手工启动/独立集成验证承担；把网络塞进 CI 会让 Yahoo 限流变成假红灯。fixture 只用虚构组合与成本数据，真实持仓不得入库。

上面三层各自 patch 掉自己那几个调用点，但「离线」若只靠每个用例自觉，漏一处或将来新增一条抓取路径就会静默出网。因此 `conftest.py` 另设 autouse 的 `_deny_network` 作为兜底：`socket.connect` / `connect_ex` / `getaddrinfo` / `subprocess.Popen` 四个口一并拦，命中抛 `NetworkUseInTests`。堵四个而不是一个，是因为本项目出网路径互不相干——引擎 `urllib` 直连 Chart、yfinance 兜底、`curl` 子进程抓东财、gspread 连 Sheets，只拦 `urllib` 剩三条照样出去。回环必须放行：Windows 上 asyncio 用 `socketpair()` 做自管道，连它一起拦会把 `AppTest` 打死，那是自伤不是防护。守卫自身由 `test_offline_guard.py` 反向罩住（四个口各一条 + 回环放行 + 异常类型），否则它退化成空壳时套件仍会全绿。

同一个 `conftest` 里另有一条 autouse 的 `_quarantine_logging`，管的是另一种越界——**测试往运行时日志文件里写东西**。`AppTest` 会真的执行 `app.py`，其中 `setup_logging(CODE_DIR / "logs")` 指的是**工作树里的** `logs/`，于是 `test_storage.py` 造的假异常（`err=network down`、`err=create transactions_bak boom`）和线上真故障进了同一个文件，日后排查分不清哪行是真的。手法是**预占 `dca` logger 的 handler 槽位**（塞一个 `NullHandler`），让 `setup_logging` 撞上它自己的幂等守卫直接返回——复用被测代码已有的行为，而不是 patch 掉它，被测路径因此保持原样。需要真配置的 `test_obs.py` 自己有 `clean_dca_logger` 把槽位清空取回真实行为。

（复核于 2026-08-21）

---

## 6. 认证链深挖（src/ui/auth.py，355 行）

> app.py 侧仅剩 :42 一行 `CURRENT_USER = auth.require_user()`，本节锚点全部在 auth.py。

三阶段状态机，全部走 `st.session_state`：

| 阶段 | 触发 | 做什么 |
|---|---|---|
| `login` | 默认 | 名字 + PIN 校验 → `storage.authenticate()` |
| `activate` | 账号存在但未激活 | 首次设 PIN → `storage.set_pin()` |
| `bootstrap` | users 表为空 | 首个注册者自动成为 admin → `storage.create_user()` |

区间构成：登录/激活/自举页渲染函数 `_render_login_page()` 定义于 auth.py:23；门闸入口 `require_user()` 在 :171（零参数，全部输入走 storage / session_state / 环境变量）；fail-closed 认证模式判断在 :177–179 一带（`DCA_AUTH_MODE=local` 才进单机模式，secrets 缺失/损坏 :194 `st.stop()`）；登录页渲染前的名单读取同样 fail-closed（:330–339，读不出名单直接报错停住，绝不渲染"创建管理员"表单）；登录门闸执行在 :341–343（`with _login_ph.container(): _render_login_page(...)` + `st.stop()`）；:344–354 是登录成功后的**会话首同步**（`storage.sync_local(user)`，同步失败不阻塞但给可见警告）；:355 返回用户名。

**认证链是全项目日志埋点最密的一段（9 处）**，因为它原本是失败最不透明的一段：4 处 `except Exception:` 把一切塌缩成"网络异常，请稍后重试"，3 处 `contextlib.suppress(Exception)` 把 `sync_local` 失败整个吞掉，全都不保留 `e`。现已改成 `except Exception as e:` + `_log.*`，**控制流一行没改**（该吞的仍然吞、该 `st.stop()` 的仍然停），只是异常不再就地销毁。安全边界见 §12：这条链离 PIN 只有一行之隔，日志只记账号名与结果码。

**两段式设计（踩过 3 轮坑，不要动）：** 点击那一趟**零网络 I/O**（用户名单取 session 缓存），把意图写进 `session_state["_auth"]` → `ph.empty()` 把登录页从 DOM 里**真删除**（不是遮住）→ 挂 `show_auth_mask` → `st.rerun()`。下一趟才在遮罩后面做全部网络工作。

**两个具体的坑**：

1. 以 `st.rerun()` 结束的运行不会清除该趟未重新渲染的旧元素 → 登录表单残留并漂在主应用上
2. 遮罩必须写 `background` —— 曾因遮罩透明导致残留登录页透出，**DOM 检查全过但用户看到冻屏**。验证遮罩不能只看元素在不在 DOM，要查 computedStyle 背景不透明度或截图

（行号复核于 2026-08-21）

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

服务函数在 `src/services/`（`run_model` model.py:24、`parse_wide_table` model.py:64、`fetch_xau_spot` quotes.py:18、`fetch_btc` quotes.py:64、`_load_json` curves.py:19、`load_price_series` curves.py:28、`tx_csv_for` curves.py:44（按用户裁决成交账本路径，与引擎 `--user` 同一规则）、`portfolio_curve` curves.py:57；全部显式收 `paths: Paths` 参数，`Paths` 定义于 `src/context.py`）；执行点在 `src/ui/sidebar.py` render() 内（首跑 :124、表单提交后金额重跑 :235），app.py 侧仅剩 :45 一行调用，结果收口为 `Decision` 返回值（`src/context.py`）后解包。下游 tab1/tab2/tab3 分别在 `src/tabs/today.py` / `holdings.py` / `records.py`（result/dec/ms/pf 等数据全部由 app.py 以显式参数传入 render()）。

**模型会跑两次，但第二遍不再白跑**：侧栏先 `run_model(None)` 自动定额；用户在 `amount_form` 表单里提交金额后再 `run_model(amount_in)` 整体重跑一遍子进程——重跑趟命中引擎的行情快照（`data/quote_snapshot.json`，TTL 600 秒，`--snapshot-ttl` 可调，0 禁用；任一标的抓价失败当趟不落盘），跳过 8 个串行行情请求与缓存增量写（下次冷跑自动追平），实测第二趟耗时约为首趟的 11%。引擎输出顶层键 `quote_snapshot`（used/age_s/ttl_s）自报快照命中情况。

（行号复核于 2026-08-19）

---

## 8. 全局耦合实测清单

> ⚠️ **拆分前基线**（1559 行版 app.py，2026-08-18 上午实测）。app.py 现已收口为 70 行纯装配层——本表保留为历史基线不再重测。收口后 app.py 模块级全局仅剩 6 个（`_paths` / `CODE_DIR` / `DATA_DIR` / `ASSETS` / `BACKTEST_DIR` / `CURRENT_USER`），全部是装配参数，业务代码零引用模块级全局。
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

**session_state 当前实测（2026-08-20，拆分后）**：**11 个键、60 处引用**，`app.py` 侧已归零 —— `src/ui/auth.py` 39 处（`_auth` 8 / `synced` 7 / `_login_err` 6 / `_names` 5 / `activating` 5 / `user` 4 / `_boot_err` 2 / `_act_err` 2）、`src/tabs/records.py` 18 处（`pending_tx` 9 / `tx_dup` 4 / `pending_obs` 3；`tx_dup` 由 `BUG-008` 的写入去重确认引入，键数因此由基线的 10 变 11）、`src/ui/sidebar.py` 3 处。

拆分后**出现了基线时不存在的跨模块共享**：`synced`（语义="本会话是否已从云端同步过"）由两个模块共管生命周期 —— `auth.py` 在登录/激活/自举成功处与会话首同步后置 True（:226 / :261 / :293 / :319 读 / :326 设），`sidebar.py` 的 🔄 刷新 `pop` 掉它触发重同步（:254）、本地历史上传后又置 True（:332）。登出走 `sidebar.py:97` 的 `st.session_state.clear()` **全清**，所以切用户不会残留 `pending_tx` 之类的待确认数据（已实测，不是数据安全问题）；代价是"哪个键属于哪条链、谁负责清"完全隐式，无单一记录处 —— 缺口已登记 `BUG-032`。

**storage 接口 19 个公开函数**（app.py 侧调用次数）：`sync_local`×4、`read_rows`×2、`list_users`×2、`is_admin`×2、`append_row`×2、`sheets_status`×2，其余 12 个各 ×1，`sheets_enabled` 仅 storage 内部使用 —— 已是干净边界。

（复核于 2026-08-18；`session_state` 当前实测于 2026-08-20，模块级全局表仍为拆分前基线不重测）

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

## 11. 计算引擎接口（scripts/dca_calculator.py，1391 行）

**参数五个**：`--amount`、`--base-dir`、`--history-years`、`--user`（多用户模式：记账数据从 `data/users/<user>/` 读取，config 与行情缓存保持共享；含路径穿越防护）、`--snapshot-ttl`（行情快照 TTL 秒数，默认 600，0=禁用。没有 `--no-refresh`）。

**输入文件**：`data/config.json`、`data/market_history/`（已收盘定稿收盘价）、`data/market_live.json`（当日未收盘 bar，加载时 merge 进序列且 csv 优先，见 §4）、`data/quote_snapshot.json`（存在且未过期时免抓价）、`data/fx_last.json`（汇率上次成功值兜底，分字段带 `fetched_at`）、记账三件套——无 `--user` 时读 `data/{transactions,observations}.csv` + `data/budget_overrides.json`，有 `--user` 时读 `data/users/<user>/` 下同名文件。

**抓取层**：`fetch_json(url, timeout=20, attempts=3)`（:228）—— 0.8s/1.6s 退避，末次失败抛最后一个异常。`fetch_history(symbols, years, cache_dir, live_path)`（:585）**并发**抓全部标的（`ThreadPoolExecutor`，`max_workers=min(8, N)`；单标的抛异常收成 `error` 条目，不带走整批），main 里行情与两个汇率**同波提交**（:1257）。`market_live.json` 的**读在线程内**（只读安全）、**写在 join 之后统一一次**（全标的共用文件，线程内写会互相覆盖）；`get_symbol_history` 用私有键 `_live_bars` 把当日 bar 回传，`fetch_history` 出口 `pop` 掉——不会进 JSON 输出也不会进快照，总耗时从 8 请求求和（最坏 160s，紧贴 subprocess 180s）变成取最大值（实测 1.5s，最坏约 62s）。

**行情快照**：`load_quote_snapshot()`（:702）/ `save_quote_snapshot()`（:720），落盘 `data/quote_snapshot.json`（`fetched_at` / markets 摘要 / `usdcny` / `usdtusd` / `fx` 段，仅几 KB）。TTL 内命中则跳过行情抓取与缓存增量写——下次冷跑自动追平，不丢历史；任一标的抓价失败（带 `error`）当趟不落盘，防止坏快照连环命中。快照存 markets 原文，故 `latest_source` / `quote_time` 两个新键自动随快照复用，闸在 TTL 内照样判得准。快照只是加速缓存：过期自动失效、缺失自动全抓，不入库。

**汇率链**（汇率是变量，全项目无一处写死常量）：`fetch_usdcny()`（:634）/ `fetch_usdtusd()`（:646）抓不到返回 `None`；抓取成功落盘 `data/fx_last.json`（`save_fx_last` :670，分字段只覆写成功的那个），失败时 `_fx_entry()`（:688）回落上次成功值，输出 `fx.{usdcny,usdtusd}.{value,live,as_of}` 三件套——连上次值都没有则 `value=null`，估值层（`portfolio_summary` :782-855）据此把 RMB 估值置空而不是编数（决策金额不依赖汇率，照出）。

**决策新鲜度闸**：`market_freshness()`（:969）在 `build_decision` 之后过闸（:1307）——主闸判 `latest_source != "quote"`（本次没拿到实时价）、副闸判 K 线落后 > `_MAX_STALE_DAYS`（:966，10 天），三个信号标的任一被拦或带 `error` → 金额归零 + `level_label="行情不可用·暂停出金额"`，原评分保留在 reason 里供参考。结果挂 `decision.degraded` 与 `decision.freshness.{stale_days,max_stale_days,per_symbol,reason}`，**不新增顶层键**（UI 与 Skill 的既有解包不受影响）。判据与两闸分工见 §4。

**输出**：print 一大段 JSON（:1387），**18 个顶层键**——字面量构造 17 个（:1367–1385：`as_of` / `input_amount_rmb` / `effective_amount_rmb` / `usdcny` / `usdtcny` / `fx` / `monthly_budget_status` / `config` / `has_local_transactions` / `invalid_transactions` / `last_records` / `since_last_record` / `markets` / `quote_snapshot` / `portfolio` / `decision` / `suggested_weights`），随后 :1373 追加 `wide_table_markdown`（由 `render_wide_table()` :1097 生成，app.py 侧 `parse_wide_table()` 解析回 DataFrame，src/services/model.py:64）。

单独跑它：

```bash
.venv/Scripts/python.exe scripts/dca_calculator.py --base-dir .
.venv/Scripts/python.exe scripts/dca_calculator.py --amount 5000 --base-dir .
```

（行号复核于 2026-08-20）

---

## 12. 运行日志：为什么长这样（src/obs.py，62 行）

配置集中在 `src/obs.py`，`app.py:31` 启动时调一次 `setup_logging(CODE_DIR / "logs")`——**摆在 `storage.init` 之前**，否则首次读写就失败时那条日志无处落。

**只配 handler，不提供 emitter。** 各模块自己 `logging.getLogger("dca.<频道>")`（现有三个：`dca.storage` / `dca.auth` / `dca.model`）。这个选择是为了避开一次层次倒挂：`storage.py` 是数据层，如果要用 `src/obs.py` 提供的 emitter，它就得 `import src`，反向依赖业务层。改成"配置在一处、取用在各处"之后，数据层只 `import logging`。

**频道名写死 `dca.*`，不用 `__name__`。** `storage.py` 是顶层模块，`__name__` 就是 `"storage"`，不在 `dca` 子树下——用 `__name__` 就没法"配一次 handler 全覆盖"，只能去配 root logger，而那会把 gspread / urllib3 / streamlit 的噪声全引进来，还会被 Streamlit 自己的 handler 打成重复行。代价是频道名不能靠 IDE 重命名跟着走，需要人记住这条约定，写在本节与模块头注里。

**三条硬约束，少一条日志就废掉一半：**

| 约束 | 不做的后果 |
|---|---|
| `setup_logging()` 幂等（`dca` logger 已有 handler 即返回） | Streamlit 每次交互整脚本重跑，handler 不去重则**同一行打 N 遍**，N 随会话交互次数增长——看起来像"系统疯了" |
| 文件 handler 带轮转（`RotatingFileHandler`，1 MB × 3） | 这正是 BUG-017 原文的另一半"日志无上限写满磁盘"，不轮转等于把刚删掉的毛病请回来 |
| `propagate = False` | 向 root 冒泡会被 Streamlit 的 handler 再打一遍 |

`propagate = False` 有一个连带影响必须知道：**pytest 的 `caplog` 收不到这些日志**（它挂在 root 上）。`tests/test_obs.py` 因此自带一个直接挂在 `dca` 上的 `_Capture` handler，别照着别处的写法用 `caplog`，那会得到一个永远为空的断言。

**两个落点各有不可替代的理由**：stderr 是 Streamlit Community Cloud 唯一会收进日志面板的东西；`logs/dca.log` 是本机长期部署要的持久化。落盘目录不可写时只记一条 warning 继续跑——stderr 那路仍在，日志能力降级但不消失。

**行格式是约定而非框架**：`事件名 key=value`，例 `sheet_read_failed table=transactions err=quota exceeded for this minute`。十来个调用点用约定足够，包一层结构化 emitter 换来的一致性抵不上多一层耦合。

**安全边界**：认证链的日志离 PIN 只有一行之隔（`_auth["pin"]` 就在同一个 dict 里），一次手滑就会把明文 PIN 写进 `logs/dca.log` 和 Cloud 日志面板，且**不会有任何报错**。所以这条不靠 review 靠断言：`test_obs.py` 用 AST 抓出全部 `_log.*(...)` 调用点，逐个查引用的变量名与字符串常量，撞到 `{pin, pin2, pin_hash, salt}` 即红，并带 `len(calls) >= 10` 下限防止扫描逻辑失效后变成空过。

**能力边界**（写在这里免得下次误判）：Cloud 日志面板只留近期、容器重启即失，`logs/dca.log` 在 Cloud 上同样是临时的；且**没有任何告警**——出事仍然要有人去开页面才知道。现有能力是"出事当场能查真因"，不是"长期留存 + 主动告警"。

（复核于 2026-08-21）

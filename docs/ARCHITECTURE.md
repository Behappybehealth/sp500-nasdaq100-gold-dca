# 工程架构说明书

> 【活文档 · 更新时机：架构变动必须同期更新】索引见 [docs/README.md](README.md)。
> **本文件是本项目架构的唯一事实源。** 首版 2026-08-17。
> 面向"想搞懂这个项目是怎么搭的"，不假设读者熟悉 Web 工程术语，第一次出现的概念都解释。
> 问题与缺陷不写在这里，走 [BUGLIST.md](BUGLIST.md)，本文只在受影响处标 `→ BUG-0XX`。

**改架构的人必读的四条维护约定：**

1. **架构一有变动，本文件同期更新** —— 不是事后补，是同一次改动里一起改。
2. **详尽程度对齐首版** —— 新增/改动一个模块，要交代清楚：它是什么、谁调用它、它调用谁、数据怎么进怎么出、**为什么这么设计、代价是什么**。只写"新增了 X 模块"不算。
3. **术语第一次出现要解释**，读者不一定是写代码的人。
4. **改完在文末「变更记录」加一行**：日期 / 改了什么 / 为什么。文档与代码冲突时代码为准，然后立刻修文档。

---

# 第一部分：这个项目是用什么技术搭的

## 1.1 技术栈全表

| 层 | 用的技术 | 一句话说明 |
|---|---|---|
| 语言 | **Python 3.14.4**（本机 `.venv`） | ⚠️ 比 `requirements.txt` 声明的下限高很多，见 §1.8 |
| 网页框架 | **Streamlit 1.61.1** | 把 Python 脚本直接变成网页的工具。**不是** Django/Flask 那种传统框架，见 §1.2 |
| 前端 | Streamlit 自带（内部是 React） | 你不写 HTML/JS。定制样式靠 `st.markdown(..., unsafe_allow_html=True)` 注入 CSS |
| 数据处理 | pandas 3.0.5 + numpy 2.5.2 | 表格运算。⚠️ pandas 3 是大版本，与 pandas 2 有不兼容改动 |
| 数据存储 | **Google Sheets**（`st-gsheets-connection` + `gspread 5.12.4`） | 把一个 Google 表格当数据库用，见 §1.4 |
| 存储回退 | 本地 CSV 文件 | 没配 Google 凭据时自动降级成单机模式 |
| 行情数据 | Yahoo Finance Chart v8 接口（`urllib` 直连）+ `yfinance 1.6.0` 兜底 + 东方财富 push2（`curl` 子进程） | 三个来源，**都没有 API key，都是非官方接口** |
| 用户认证 | 自己写的"名字 + PIN" | PIN 用 PBKDF2-HMAC-SHA256（20 万迭代 + 每账号随机盐）存在 Google Sheets 的 users 表里；旧 SHA256 账号登录成功时自动迁移 |
| 计算引擎 | 独立 Python 脚本 + **subprocess** | 见 §1.3 |
| 部署（生产） | **Streamlit Community Cloud** | 推送到 GitHub main 分支自动重新部署 |
| 临时外发 | ngrok 固定域名 | 本机开着时把 8501 端口发到公网 |
| 版本控制 | git + GitHub 私有仓库 | `Behappybehealth/sp500-nasdaq100-gold-dca` |
| 代码格式 | ruff | 有格式化痕迹，但**没有强制检查**（无 CI） |

## 1.2 理解一切的钥匙：Streamlit 的"脚本重跑"模型

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
| 重跑一遍，之前用户填的东西不就丢了？ | **`st.session_state`** —— 一个字典，跨"重跑"活着，每个浏览器标签页各有一份 | 存登录状态 `user`、待确认的记账 `pending_tx` 等 11 个键 |
| 重跑一遍，慢操作（抓行情、算模型）每次都重做？ | **`st.cache_data`** —— 记住函数的结果，下次同样参数直接返回 | `run_model()` 缓存 900 秒 ← **`BUG-001` 就出在这里** |
| 重跑一遍，怎么让页面停在登录页不往下走？ | **`st.stop()`** —— 立刻停止本次执行 | 认证门闸用它拦住未登录用户 ← **`BUG-003` 出在这里** |

> **最关键的一条：`st.session_state` 是"每个浏览器一份"，但 `st.cache_data` 是"整个服务器共用一份"。**
> 这个区别很容易搞混，也是 `BUG-001` 的全部原因。

**还有一个必须知道的怪癖**：以 `st.rerun()` 结束的运行**不会清除该趟未重新渲染的旧元素**。所以瞬态页面（比如登录页）必须整体挂进一个固定的 `st.empty()` 容器，跳转前用 `容器.empty()` 把它从 DOM 里**真删除**，而不是用遮罩盖住 —— 否则登录表单会残留并漂在主应用上。这个坑踩过三轮，细节见 §2.4-A。

## 1.3 为什么计算用"子进程"（subprocess）

`app.py` 自己不算策略。它是这样做的：

```
app.py  ──启动一个全新的 Python 程序──→  scripts/dca_calculator.py
        ←──── 对方把结果打印成 JSON 文字 ────
        （app.py 读这段文字，转成数据用）
```

用代码看就是 app.py:558–572：

```python
cmd = [sys.executable, "scripts/dca_calculator.py", "--base-dir", str(BASE)]
out = subprocess.run(cmd, capture_output=True, timeout=180)
return json.loads(out.stdout)          # 把对方打印的 JSON 转成 Python 字典
```

**打个比方**：不自己做饭，打电话叫外卖。好处是厨房（引擎）和餐厅（UI）完全隔离——改菜谱不会弄坏餐厅装修。**这个设计是对的，也是这个项目最干净的一处。**

代价是：两边只能通过"命令行参数"和"打印出来的文字"沟通。**引擎不知道谁是当前用户**，它只知道 `--base-dir` 指向哪个目录、那个目录里的 CSV 写了什么。这是 `BUG-001` 的另一半原因。

## 1.4 为什么用 Google Sheets 当数据库

好处很实在：不用买服务器、不用装数据库、你能用手机打开表格直接看数据、免费。

代价是**它没有"改一行"的能力**。真正的数据库你可以说"把第 5 行的金额改成 1000"。Google Sheets 这套库只能：

- 读：把**整张表**拉下来
- 写：把**整张表**推上去（覆盖）

所以"加一笔成交记录"的实际动作是（storage.py:304–306）：

```
把整张 transactions 表读下来（比如 200 行）
  → 在末尾拼上你这 1 行（变 201 行）
  → 把这 201 行整体推上去，覆盖原来的 200 行
```

**打个比方**：改一个字，要把整本书重新抄一遍再交上去。**这是 `BUG-002` 的全部原因**——如果"读下来"这一步失败了，你手里就是一本空书，抄完交上去，原来那本就没了。

**四个工作表**（storage.py 文件头 docstring）：

| 工作表 | 字段 |
|---|---|
| `users` | `name, pin_hash, salt, hash_algo, role, fail_count, locked_until, created_at`（旧 4 列行由 `_read_ws` 补空串兼容） |
| `transactions` | 成交记录，含 `user` 列 |
| `observations` | 跳过/观察记录，含 `user` 列 |
| `budget_overrides` | 月度预算覆盖，含 `user` 列 |
| `<任意表>_bak` | 写前快照（滚动单份）：`_write_ws` 覆写主表前先把现内容推到这里，快照失败则放弃写入（BUG-002 修复） |

所有数据表都带 `user` 列做行级隔离。另有**进程内全局 8 秒短缓存** `_SHEET_CACHE`（storage.py:96–97），`_read_ws(..., fresh=True)` 可绕过它强制新鲜读。

## 1.5 行情缓存的"增量"是什么意思

十年历史行情不可能每次都重抓。所以缓存是这样的（`data/market_history/*.csv`，两列 `date,close`）：

```python
dca_calculator.py:312-317   period1 = last_cached   # 从缓存里最后一天开始抓
dca_calculator.py:292-298   落盘时 open("w") 整文件截断重写 + 排序
```

`period1 = last_cached` 意味着**最前沿那一天每次运行都会被重抓并按日期键覆盖**。

- **好处**：当天的价格会自动从盘中价修正成收盘价（自愈一天）
- **代价**：只要有更晚的日期落库，前一天就再也不会被重新请求 —— **脏值永久冻结**。→ `BUG-006`

**抓取路径与降级顺序**：

```
Yahoo Chart v8（urllib 直连，20s 超时，0 次重试）
   ↓ 失败且有缓存
标 data_source="cache_stale" + warning（不再尝试 yfinance）
   ↓ 失败且无缓存
yfinance 兜底（auto_adjust=True）

东财 push2（curl 子进程，3 次重试无退避）→ XAU / BTC 实时价
```

⚠️ 两条路径**复权口径不一致**：Chart 走原始 `quote.close`，yfinance 兜底用 `auto_adjust=True`。→ `BUG-007`

## 1.6 两个使用入口

| 入口 | 文件 | 说明 |
|---|---|---|
| **Web 决策台** | `app.py` | Streamlit 网页，多用户，云端存储 |
| **Claude Skill** | `X:\coding\skills\projects\sp500-nasdaq100-gold-dca\SKILL.md`（通过目录联接挂到 `~/.claude/skills`） | 对话式每日建议 |

两个入口**共用同一套计算引擎和数据**，但**不共用业务层** —— Skill 那一路绕过 `app.py` 直接 subprocess 调引擎，所以登录、预算覆盖、记账逻辑在 Skill 侧是缺失的。→ `BUG-022`

## 1.7 线上地址与平台配置

| 用途 | 地址 |
|---|---|
| 生产 | https://dca365.streamlit.app/ |
| ngrok 临时外发 | https://sudoku-manhood-argue.ngrok-free.dev |

平台侧必须配的三项（`share.streamlit.io` → 应用 ⋮ → Settings）：

- **Secrets** —— GCP 凭据，内容同本机 `.streamlit/secrets.toml`。**不在 git 里**，换机器/重建应用要手动贴
- **General → App URL** —— 自定义子域 `dca365`
- **Sharing → public** —— 否则访问者要先登录有权限的 Streamlit 账号，家人打不开

> 应用是 public 的，意味着**应用内的「名字 + PIN」门闸是唯一防线**。→ `BUG-003`、`BUG-004`

## 1.8 依赖声明与版本漂移

`requirements.txt` 全 6 行，全是无上界的 `>=`：

```
streamlit>=1.32.0        本机实装 1.61.1
yfinance>=0.2.40         本机实装 1.6.0
pandas>=2.0.0            本机实装 3.0.5   ← 跨了一个大版本
numpy>=1.24.0            本机实装 2.5.2
st-gsheets-connection>=0.1.0
gspread>=5.8.0,<6        本机实装 5.12.4  ← 唯一有上界的
```

无 lock 文件。**声明的和实装的差很远，等于没有可复现性** —— 上游一发版就可能把线上打挂。→ `BUG-015`

## 1.9 数据口径

- **交易本位**：USDT（标识 `"U"`）
- **代码**：标普500 → `SPY`，纳指100 → `QQQ`，黄金 → `XAUT`
- **估值**：SPY/QQQ 用 Yahoo 实时价 × USD/CNY；XAUT 用 `XAUT-USD` × U/CNY
- **月度预算**：默认 30000 RMB，可按月覆盖（`data/budget_overrides.json`）
- **中性权重**：SP500 35% / NDX100 45% / 黄金 20%

---

# 第二部分：架构图与数据流

## 2.1 整体架构

```
                        ┌─────────────────────────────────┐
   浏览器（用户）  ←───→ │  Streamlit Community Cloud      │
                        │  一个 Python 进程服务所有用户     │
                        │                                 │
                        │    app.py（1559 行）             │
                        │    ├─ 全局 CSS                   │
                        │    ├─ 登录门闸（名字+PIN）         │
                        │    ├─ 侧边栏（在这里跑模型）       │
                        │    └─ 6 个 Tab                   │
                        └───┬──────────────────┬──────────┘
                            │                  │
              subprocess    │                  │  import
              （子进程）     │                  │
                            ▼                  ▼
        ┌───────────────────────────┐   ┌──────────────────┐
        │ scripts/dca_calculator.py │   │   storage.py     │
        │ 计算引擎（938 行）         │   │  存储层（594 行） │
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

**三层 + 一个边界**：`app.py`（UI + 业务逻辑，耦合较紧）→ 通过 subprocess 隔离 `dca_calculator.py`（纯计算）、通过 import 使用 `storage.py`（数据层）。

## 2.2 一次"打开页面看今日建议"的完整数据流

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
④ 侧边栏调用 run_model(None, CURRENT_USER)   ← 缓存键含用户身份
      → 启动子进程 dca_calculator.py --base-dir <项目目录> --user <用户名>
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

## 2.3 app.py 一次渲染的真实时序

承 §1.2 —— `app.py` **不是模块，是一个从头跑到尾的脚本**：

```
① 1–51    解析 --base-dir → 定 BASE/DATA_DIR → storage.init() → 读 config.json → set_page_config
② 54–229  注入全局 CSS
③ 230–280 定义三个遮罩组件（show_loading / show_sync_mask / show_auth_mask）
④ 281–556 认证门闸 ←── 未登录就 st.stop()，下面的代码根本不执行
⑤ 557–731 定义服务函数（run_model / 行情 / 曲线）
⑥ 732–993 渲染侧边栏 ←── 副作用：在这里跑模型，产出 result/dec/ms/pf
⑦ 994–1005 声明 6 个 tab
⑧ 1034–1559 依次渲染 6 个 tab ←── 消费 ⑥ 产出的变量
```

**关键点：⑥ 既是 UI 又是业务入口。** 侧边栏渲染的过程中调用 `run_model()`，把决策结果留在模块级作用域，下游 6 个 tab 直接引用。这就是为什么"把 tab 搬出去"必须先解决"结果怎么传进去"。

## 2.4 三条业务链路

### A. 认证链（app.py:281–556，327 行）

三阶段状态机，全部走 `st.session_state`：

| 阶段 | 触发 | 做什么 |
|---|---|---|
| `login` | 默认 | 名字 + PIN 校验 → `storage.authenticate()` |
| `activate` | 账号存在但未激活 | 首次设 PIN → `storage.set_pin()` |
| `bootstrap` | users 表为空 | 首个注册者自动成为 admin → `storage.create_user()` |

**两段式设计（踩过 3 轮坑，不要动）：** 点击那一趟**零网络 I/O**（用户名单取 session 缓存），把意图写进 `session_state["_auth"]` → `ph.empty()` 把登录页从 DOM 里**真删除**（不是遮住）→ 挂 `show_auth_mask` → `st.rerun()`。下一趟才在遮罩后面做全部网络工作。

**两个具体的坑**：

1. 以 `st.rerun()` 结束的运行不会清除该趟未重新渲染的旧元素 → 登录表单残留并漂在主应用上
2. 遮罩必须写 `background` —— 曾因遮罩透明导致残留登录页透出，**DOM 检查全过但用户看到冻屏**。验证遮罩不能只看元素在不在 DOM，要查 computedStyle 背景不透明度或截图

### B. 决策链（app.py:557–731 定义 + 732–993 执行）

```
侧栏渲染 ──→ run_model(None) ──subprocess──→ scripts/dca_calculator.py ──→ JSON
                    │
                    └─→ result / dec / ms / pf 落在模块作用域
                                │
              ┌─────────────────┼──────────────────┐
            tab1 今日模拟      tab2 持仓曲线      tab3 记账
         (result×9 dec×6 ms×4)  (pf×8)        (result×3 dec×3)
```

**模型会跑两次**：侧栏先 `run_model(None)` 自动定额；若用户手填了金额（`amount_in > 0`），再 `run_model(amount_in)` 整体重跑一遍子进程。→ `BUG-024`

### C. 记账链（tab3 写 → tab4 读）

```
tab3  用户回报成交 → session_state["pending_tx"] 暂存 → 复述确认 → storage.append_row("transactions")
      主动跳过     → session_state["pending_obs"]              → storage.append_row("observations")
tab4  storage.read_rows("transactions") + read_rows("observations") → 两张表原样展示
```

tab4 是这条链的**读侧**，只有 14 行，业务上和 tab3 是一件事。

## 2.5 六个 Tab 的职责

| tab | 行区间 | 行数 | 业务职责 | 依赖 |
|---|---|---:|---|---|
| 🎯 今日模拟 | 1045–1124 | 80 | 今日建议金额/部署系数/三资产分配/三档执行方案 | result, dec, ms, ASSETS |
| 📊 持仓与曲线 | 1125–1188 | 64 | 持仓汇总、估值、浮盈亏、XIRR、净值曲线 | pf, ASSETS |
| ✍️ 记账 | 1189–1304 | 116 | 回报成交 / 主动跳过，二次确认后落库 | result, dec, ASSETS, CURRENT_USER |
| 📜 历史记录 | 1305–1318 | 14 | 回读 transactions / observations | CURRENT_USER |
| 🧪 回测结果 | 1319–1551 | **233** | 5 段静态回测报告（全部读 `backtest/*.json`，BUG-025 已修） | BACKTEST_DIR |
| 📖 策略说明 | 1552–1559 | 8 | 读 `strategy/core-strategy.md` 渲染（唯一事实源，BUG-026 已修） | CODE_DIR |

（行区间 2026-08-18 复核）

**tab5 内部构成**：

| 段 | 行区间 | 行数 | 数据来源 |
|---|---|---:|---|
| 一、三策略对比 | 1327–1410 | 84 | ✅ 读 `results_compare3.json` |
| 二、为什么定额等比最高 | 1412–1424 | 13 | 纯 markdown |
| 三、单品种滚动回测（含四张子表） | 1425–1514 | 90 | ✅ 读 `results_single_compare.json` + `results_rolling.json` |
| 四、四标的横向对比 | 1516–1520 | 5 | ✅ 读 `results_rolling.json` |
| 五、综合结论 | 1522–1551 | 30 | 纯 markdown |

历史上后五张表曾把 415 行数据硬写在代码里，2026-08-18 已导出 `results_rolling.json`（BUG-025），现在全 tab 统一从文件读数。

## 2.6 全局耦合清单（实测，不是估计）

**模块级全局 8 个**，作用域比想象的窄得多：

| 全局 | 总用量 | 侧栏 | tab1 | tab2 | tab3 | tab4 | tab5 | tab6 | 归属判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `CURRENT_USER` | 15 | 8 | 0 | 0 | 2 | 2 | 0 | 0 | 会话态 |
| `DATA_DIR` | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `ASSETS` | 7 | 0 | 1 | 1 | 4 | 0 | 0 | 0 | 配置 |
| `CODE_DIR` | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `BASE` | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `CONFIG` | 3 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 配置 |
| `TX_CSV` | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `BACKTEST_DIR` | 3 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | **只 tab5 用** |

`DATA_DIR / CODE_DIR / BASE / TX_CSV` 在**任何 UI 代码里都是 0 次** —— 它们只被服务函数用。这意味着 UI 层根本不需要看见路径。

**session_state key 11 个**：`synced`(5) `user`(4) `_names`(4) `_login_err`(4) `_auth`(3) `pending_tx`(2) `activating`(2) `pending_obs` `_boot_err` `_act_err`，另有 13 处 `.pop()` / 6 处 `.get()` / 1 处 `.clear()`。其中 8 个属认证链、3 个属记账链，**没有跨链共享**。

**storage 接口 19 个公开函数**（BUG-001~004 修复新增 `sheets_status` / `list_users_fresh`；`sync_local`×4, `read_rows`×2, `list_users`×2, `is_admin`×2, `append_row`×2, 其余各 1）—— 已是干净边界。

---

# 第三部分：目录与文件逐个说明

## 3.1 根目录

| 路径 | 行数 | 是什么 | 谁读它 | 入库 |
|---|---:|---|---|:---:|
| `app.py` | 1559 | Streamlit 主程序。CSS + 登录 + 侧边栏 + 6 个 Tab 全在里面 | Streamlit 直接执行 | ✅ |
| `storage.py` | 594 | 存储层。所有 Google Sheets 读写都走它（含写前快照、PBKDF2 认证） | `app.py` import | ✅ |
| `requirements.txt` | 6 | 依赖清单。全是 `>=` 不钉版本 | Cloud 装依赖时 | ✅ |
| `start-app.bat` | — | 本机双击启动 | 你 | ✅ |
| `CLAUDE.md` | — | 给 AI 编程助手的项目说明 | AI 助手 | ✅ |
| `README.md` | — | 项目自述 | 人 | ✅ |
| `.dockerignore` | 13 | **Docker 当前未启用**，保留作将来重写 Dockerfile 的安全默认（第一条排除 secrets） | 无（暂时） | ✅ |

## 3.2 `scripts/` — 计算引擎

| 路径 | 行数 | 说明 |
|---|---:|---|
| `scripts/dca_calculator.py` | 930 | **策略大脑**。完全独立可单跑，不依赖 Streamlit。输入 = CSV + config，输出 = JSON（15 个顶层键） |

单独跑它：

```bash
.venv/Scripts/python.exe scripts/dca_calculator.py --base-dir .
.venv/Scripts/python.exe scripts/dca_calculator.py --amount 5000 --base-dir .
```

**参数四个**：`--amount`、`--base-dir`、`--history-years`、`--user`（多用户模式：记账数据从 `data/users/<user>/` 读取，config 与行情缓存保持共享；含路径穿越防护。没有 `--no-refresh`）。
**引擎读的文件**：`data/config.json`、`data/market_history/`、记账三件套——无 `--user` 时读 `data/{transactions,observations}.csv` + `data/budget_overrides.json`，有 `--user` 时读 `data/users/<user>/` 下同名文件。

## 3.3 `data/` — 数据目录（引擎唯一的数据来源）

| 路径 | 是什么 | 入库 | 说明 |
|---|---|:---:|---|
| `data/config.json` | 策略参数与资产定义（权重、区间、评分系数） | ✅ | 改策略参数改这里 |
| `data/users/<用户>/` | **云端模式的每用户落盘缓存**（transactions/observations/budget_overrides） | ❌ | 由 `sync_local` 从 Sheets 覆写生成；覆盖前带时间戳轮转留底 10 份（`*.YYYYMMDD-HHMMSS.localbak`） |
| `data/transactions.csv` | **成交记录**（真实持仓的账本） | ❌ | 仅本地单机模式使用；云端模式已改 `data/users/<user>/` |
| `data/observations.csv` | 跳过/观察记录 | ❌ | 同上 |
| `data/budget_overrides.json` | 按月覆盖预算 | ❌ | 同上 |
| `data/*.localbak` | 轮转留底文件 | ❌ | 2026-08-18 起为带时间戳的真轮转（旧"只留一份"假保护已随 BUG-002 修复移除） |
| `data/market_history/*.csv` | **行情缓存**，6 个文件，两列（`date,close`） | ✅ | **入库是刻意的** —— 让 Cloud 部署不用冷启动重抓十年数据 |

**行情缓存 6 个文件**：`_GSPC.csv`（标普指数）、`SPY.csv`（标普 ETF）、`_NDX.csv`（纳指）、`QQQ.csv`（纳指 ETF）、`GC_F.csv`（黄金期货）、`XAUT_USD.csv`（黄金代币）。孤儿 `GLD.csv`（不在抓取名单、冻结在 2026-08-10）已于 2026-08-18 从仓库移除（BUG-023），取回：`git show f1ed967:data/market_history/GLD.csv`。

> ⚠️ **这是本仓库最宝贵的资产**（增量十年历史）。"能重下"≠"可以丢"。别删。→ `BUG-006`

## 3.4 `backtest/` — 回测（一次性产物，非运行时依赖）

| 路径 | 说明 |
|---|---|
| `backtest_dca.py` / `backtest_single.py` / `backtest_compare3.py` | 回测脚本。⚠️ 全部写死旧绝对路径 `C:\Users\xiezhibo\.claude\skills\...`，现在跑不起来 → `BUG-015` |
| `results_compare3.json` | Tab5 第一段读它（三策略对比） |
| `results_single_compare.json` | Tab5 第三段读它（单品种动态 vs 固定） |
| `results_rolling.json` | Tab5 第三段四张滚动表 + 第四段横向对比读它（BUG-025：2026-08-18 从 app.py 硬编码导出，33 行 × 338 格与原字面量逐格相等） |
| `results.json` / `results_single.json` / `results.md` / `compare3.md` | 中间产物与文字报告，应用不读 |

## 3.5 `strategy/` — 策略文档

| 路径 | 说明 |
|---|---|
| `strategy/core-strategy.md` | 策略说明**唯一事实源**。Tab6 启动时读它直接渲染（BUG-026 已修，内嵌副本已删） |

## 3.6 `deploy/` — 部署与外发

> **2026-08-17 已清理**：Docker 那套（`Dockerfile` / `docker-compose.yml` / `nginx.conf` / `setup_user.sh` / `streamlit-config.toml`）已删除 —— 它从未成功构建过一次（自 `574c7a7` 初始提交起一行未改），却被标为"唯一事实源"。
> 取回：`git show 574c7a7:deploy/Dockerfile`。删除理由与「将来重启 Docker 的必守清单」见 `deploy/DEPLOY.md` 第 5 节；对应的问题记录是 `BUG-012`（连带 `BUG-005` / `BUG-013` / `BUG-014`）。

| 路径 | 状态 | 说明 |
|---|---|---|
| `DEPLOY.md` | ✅ 已重写 | 只写真实在用的三条路径：Cloud（生产）/ 本机 / ngrok |
| `start-dca-tunnel.bat` | ✅ 活的 | ngrok 外发。**只能写 ASCII** —— cmd 按 OEM 码页（936）读批处理，UTF-8 中文注释会被当乱码命令执行。中文说明写进 DEPLOY.md |
| `bin/ngrok.exe` | ✅ 33 MB | **不入库**，删了只能重下。脚本靠 `%~dp0bin\ngrok.exe` 相对定位 |

## 3.7 `docs/` 与 `.streamlit/`

| 路径 | 说明 | 入库 |
|---|---|:---:|
| `docs/ARCHITECTURE.md` | **本文件**。架构唯一事实源 | ✅ |
| `docs/BUGLIST.md` | **问题台账**。每条走「梳理 → 1对1确认 → 修复 → 验证」四段 | ✅ |
| `docs/plans/` | 计划与历史审计存档（`app-split-design.md` = app.py 拆分 6 刀方案；`project-audit-2026-08-17.md` = 原始审计快照） | ✅ |
| `.streamlit/config.toml` | 主题配色 | ✅ |
| `.streamlit/secrets.toml` | **GCP 服务账号凭据**，2600 字节 | ❌ |
| `.streamlit/secrets.toml.example` | 模板 | ✅ |

---

# 变更记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-08-17 | **首版建立。** 从 `docs/plans/architecture-and-p0-explained.md` 拆出架构部分；问题部分转入 [BUGLIST.md](BUGLIST.md)（原文一条不丢，全部按 `BUG-0XX` 编号登记） | 一份文档同时讲架构和缺陷，两者更新节奏不同，必然漂移 |
| 2026-08-17 | 记录三处**已修正的文档不实**：① CLAUDE.md 与旧 DEPLOY.md 都称 Tab6 读 `strategy/core-strategy.md`，实测 `grep strategy app.py` **零命中**；② 旧 Dockerfile 声明 `python:3.12-slim`，本机实测 **Python 3.14.4**；③ 旧 DEPLOY.md 称"每个用户有独立容器和数据目录，完全隔离"，实际所有用户共用一个 `data/transactions.csv` 和一块进程级缓存 | 文档说的和代码做的不一致，比没有文档更危险 |
| 2026-08-17 | `deploy/` 删除 5 个 Docker 死文件（`Dockerfile` / `docker-compose.yml` / `nginx.conf` / `setup_user.sh` / `streamlit-config.toml`），§3.6 改写 | **主修 `BUG-012`**（Docker 那套从未成功构建过，自初始提交零迭代，却被标为"唯一事实源"）。**因为下面三个问题的成因全部落在被删的文件里，同一个动作连带修复了**：`BUG-005`（GCP 私钥被 `Dockerfile:21` 的 `COPY .streamlit/` 打进镜像层）、`BUG-013`（容器隔离与应用内登录两套多用户实现互相抵消）、`BUG-014`（`setup_user.sh:90` 的 sed 会把 nginx location 插到 server 块外面，加一个用户炸掉所有用户）。四条记录连带关系与验证结果都在 BUGLIST 里，**一条都没删** |
| 2026-08-18 | **P0 清舱（BUG-001~004，commit `a1707a6` + `f02ff22`）**：① 认证门闸 fail-closed——`AUTH_MODE` 默认 `sheets`，secrets 缺失/损坏即拒启动，单机必须显式 `DCA_AUTH_MODE=local`；② 多租户隔离补齐另一半——`run_model` 缓存键含用户、引擎新增 `--user` 读 `data/users/<user>/`、`sync_local` 分目录落盘；③ 存储层 "empty ≠ error"——读故障抛 `SheetReadError`、写前快照 `<表名>_bak`（快照失败放弃写入）、本地轮转留底 10 份；④ PIN 升级 PBKDF2+随机盐、连续失败 5 次锁 15 分钟、旧 sha256 账号登录自动迁移、新 PIN 强制 6-8 位 | 四条 P0 全部经 1 对 1 确认后施工，43 项离线假连接测试全过 + 引擎双模式回归 + AppTest 双模式冒烟；确认记录/改动清单/验证输出均已回填 BUGLIST |
| 2026-08-18 | **BUG-026+021 修复**：`strategy/core-strategy.md` 全量重写为 184 行合并版（技术骨架 + 原 Tab6 独有的家人友好开场、§4 闭环图、§8 回测结论诚实版、§12 隐私真话版）；`app.py` Tab6 删掉 75 行内嵌副本改为读文件渲染（1967–1974），app.py 2041→1974 行 | 同一份策略说明两个副本必然漂移（026）；隐私声明写的是"数据不上传任何地方"，实际全部存 Google Sheets（021）。现在 Tab6 直接渲染唯一事实源，改文档即改页面 |
| 2026-08-18 | **BUG-023 修复**：删三处死定义——`verify_user`（storage.py，全项目零调用）、`append_csv`（app.py，定义后从未调用）、`OBS_CSV`（app.py，定义后从未使用）；孤儿 `GLD.csv` 从仓库移除（git 可取回）。storage.py 603→594 行、app.py 1974→1964 行、公开接口 20→19、模块级全局 9→8、行情缓存 7→6 个文件 | 死物让人误以为功能还在，顺着改会改到空气；孤儿文件不留中间态。修复路径与 GLD 删除均经用户拍板 |
| 2026-08-18 | **BUG-025 修复**：Tab5 五块硬编码回测数据（sp500/ndx/gold/hs300 四张滚动表 + 四标的横向对比，共 33 行 × 338 格）AST 无损导出为 `backtest/results_rolling.json`；app.py 改为统一读文件 + 缺失时 warning 优雅降级；结尾 caption 从失效的 `backtest-dca-5y/` 改指 `backtest/`。app.py 1964→1559 行（tab5 638→233 行） | 代码仓库不放数据：改回测不再触发代码部署；单一供数方式消除"两个事实源哪个新"的问题 |

# 项目级审计与优化全景图

> 2026-08-17。覆盖 12 个面。所有结论都带 `文件:行号` 证据。
> **历史审计快照：编号和状态停留在审计当天，不再作为当前台账维护。** 当前架构事实源见 [`../ARCHITECTURE.md`](../ARCHITECTURE.md)，当前问题编号、状态、确认路径和验证记录见 [`../BUGLIST.md`](../BUGLIST.md)。
> 这份文档在审计当天取代 `app-split-design.md` 的优先级；其中面向人的讲解后来拆入上述两份活文档。

---

## ✅ 审计当天已解决（2026-08-17，一次删除）

主修当时的 P1-6：删掉 `deploy/` 里从未跑通的 Docker 那套（`Dockerfile` / `docker-compose.yml` / `nginx.conf` / `setup_user.sh` / `streamlit-config.toml`）。同一个动作连带修复或部分收敛了下列条目；**问题记录并未删除**，当前编号和状态见 [`../BUGLIST.md`](../BUGLIST.md)：

| 原编号 | 问题 | 为什么删就解决了 |
|---|---|---|
| **P0-4** | GCP 私钥烤进镜像层 | 没有 Dockerfile → 没有 build → 没有镜像。成因消失，比修更彻底 |
| **P1-6** | Docker 那套从未跑通却被标"唯一事实源" | 死物已移除，DEPLOY.md 重写为只写真实在用的三条路径 |
| **P1-7** | 两套多用户实现互相抵消 | 只剩应用内登录 + Sheets 行隔离这一套 |
| **P1-8** | `setup_user.sh` 一个新用户炸掉所有用户 | 脚本已移除 |
| P2-1 部分 | 容器 nginx 签不下证书 | 这一半消失；"无自有域名"仍在，但现在纯粹是 Streamlit Cloud 的事 |
| P2-2 部分 | 容器 json-file 日志无上限写满磁盘 | 这一条消失 |

保留 `.dockerignore` 并把 `.streamlit/secrets.toml` 列在第一条，作为将来重启 Docker 的安全默认。
取回旧文件：`git show 574c7a7:deploy/Dockerfile`。重启清单见 `deploy/DEPLOY.md` 第 5 节。

**剩余：P0 × 4（P0-1/2/3/5）、P1 × 6、P2 × 7。**

---

## 0. 一句话结论

这个项目的**功能**是好的（策略引擎干净、UI 完整、回测扎实），但它的**工程地基有 5 个 P0 级破口**，其中 3 个不需要任何人攻击、会自己发生，1 个每天都在发生。

| 级别 | 数量 | 判据 |
|---|---:|---|
| **P0** | 5 | 数据会丢/会串号，或鉴权会失效。**不需要外部触发** |
| **P1** | 9 | 会给出错误的钱数，或让部署/运维在关键时刻失败 |
| **P2** | 8 | 长期负债，包括 app.py 拆分 |

---

# P0 · 五条必须先堵的破口

## P0-1 ｜跨用户数据串号：用户 B 会看到用户 A 的持仓和建议金额

**这是全项目最严重的问题，而且在 Streamlit Cloud 上每天都会触发。**

两条独立机制，任一条都足以串号：

**机制 A — 进程级缓存不含用户身份**

```python
app.py:556   @st.cache_data(ttl=900, show_spinner=False)
app.py:557   def run_model(amount: float | None) -> dict:      ← 缓存键只有 amount
```

`st.cache_data` 是**进程级**的，跨会话共享。于是：

```
用户 A 登录 → sync_local(A) 把 A 的成交写进 data/transactions.csv
           → run_model(None) 算出 A 的决策 → 以键 (None,) 缓存 900 秒
用户 B 登录 → sync_local(B) 把文件覆盖成 B 的成交
           → run_model(None) → 缓存命中 → 拿到 A 的结果
```

B 看到的持仓、市值、浮盈亏、XIRR、今日建议金额，**全是 A 的**。

**机制 B — 所有用户共享同一个数据文件**

```python
app.py:26-34    BASE = --base-dir 或 CODE_DIR；DATA_DIR = BASE / "data"
                ↑ Cloud 上没有 --base-dir → 所有用户都是同一个 repo/data
storage.py:428-438  _LOCAL_BASE = init(data_dir) 传进来的那一个目录
storage.py:368-388  sync_local(user) 把 transactions.csv 整体覆写成该用户的数据
app.py:549-552  每会话首次进入时调用 sync_local(CURRENT_USER)
app.py:561-562  run_model 把 --base-dir str(BASE) 传给子进程
```

两个用户同时在线 = 两个子进程读同一个正在被对方覆写的 CSV。**竞态窗口是整个 sync + 计算过程。**

**后果**：不是"看到别人一点数据"，是**整个决策基于别人的持仓**。用户按这个金额真去下单。

**修法方向**：缓存键带上用户；每用户独立数据目录（Docker 那套已经这么做了，Cloud 这套没有）。这两件都要做，只做一件不够。

---

## P0-2 ｜认证是 fail-open 的：门闸可以整体消失

```python
app.py:430   CURRENT_USER = "local"
app.py:431   if storage.sheets_enabled():      ← 整个登录门闸在这个 if 里面
                 ... 名字+PIN 校验 ...
                 st.stop()
```

而这个开关吞掉一切异常：

```python
storage.py:63-68   def sheets_enabled():
                       try:  return "gsheets" in st.secrets.get("connections", {})
                       except Exception:  return False       ← 任何异常都返回「关」
```

**secrets 读取出任何问题 → 返回 False → 门闸从执行路径上消失 → `CURRENT_USER = "local"` → 任何访客直接进主应用。**

零日志、零启动断言、UI 上没有任何区别。安全控制朝着**不安全**的方向失效。

**触发条件**：Cloud 后台误删/误改 secrets、GCP 凭据过期、`st.secrets` 任何读取异常。而应用已经改成 public，这道门是唯一防线。

**修法方向**：启动期断言——明确要求一个 `AUTH_MODE` 配置，值为 `sheets` 时 secrets 缺失必须**崩掉**而不是降级。fail-closed。

---

## P0-3 ｜一次 Sheets 读取抖动 = 全量历史被覆写

```python
storage.py:107-110   _read_ws() 用 except Exception 兜住一切 → 返回「带表头的空表」
                     ↑ 「读失败」和「表真的是空的」在返回值上完全无法区分
storage.py:299-307   append_row() 拿到空表 → concat 一行 → _write_ws() 整表覆写
```

你记一笔账 = 整张 transactions 表被这一行覆盖。**历史全灭。**

同一根引信的第二条路（走鉴权）：

```
app.py:542       names = storage.list_users()          ← 读失败返回 []
app.py:540-546   名单为空 → 登录页渲染「首次自举」表单（任何访客都能看到）
app.py:507-510   提交时防呆是再读一次 list_users_fresh()
                 ↑ 读还在失败，_fresh 依然是 [] → 防呆失效
storage.py:205   create_user() → _write_ws("users", concat([空表, 新 admin]))
                 → 访客成为 admin，users 表被覆写成只剩他一行
```

**那个防呆只防"会话名单过期"，不防"读失败"。**

**没有恢复路径**：Google Sheet 无备份；本地 `.localbak` 是假保护——[storage.py:378-380](storage.py#L378-L380) 的条件是"`.localbak` **不存在时**才备份"，所以备份只在第一次 sync 时生成过一次，永远是最早那份；而且用的是 `path.replace()`（**移动**不是复制），移动完到重新写出之间崩掉，当前文件就没了。

**修法方向**：`_read_ws` 区分失败与空——读失败抛异常，让上层停住而不是继续；写前做一次 Sheet 快照（另一个 worksheet 或导出）；`.localbak` 改成带时间戳的滚动备份 + 复制而非移动。

---

## P0-4 ｜GCP 私钥会被烤进 Docker 镜像层 —— ✅ 已解决（删除 Docker 那套）

```
.dockerignore 全文 8 行：data/ __pycache__/ *.pyc .git/ .claude/ backtest*/ *.md !deploy/
                        ↑ 没有排除 .streamlit/
deploy/Dockerfile:21    COPY .streamlit/ .streamlit/        ← 整目录
开发机实况               .streamlit/secrets.toml 存在，2600 B，含真实 private_key
```

**`.gitignore` 护得住 git，护不住 `docker build`。** 镜像推到任何 registry 或分享给任何人，服务账号私钥跟着走。

**修法方向**：`.dockerignore` 加 `.streamlit/secrets.toml`；secrets 改成运行时挂载或环境变量注入，不进镜像层。

---

## P0-5 ｜登录门闸的强度撑不住 public 部署

| 事实 | 证据 |
|---|---|
| PIN 只要 4–8 位 | [storage.py:190](storage.py#L190) `4 <= len(pin) <= 8` |
| 哈希是**单轮无盐 SHA256** | [storage.py:134-135](storage.py#L134-L135) `sha256(f"dca::{name}::{pin}")` |
| 唯一的"盐"是用户名——而用户名在登录页**下拉可枚举** | [app.py:540-546](app.py#L540-L546) 名单渲染给未登录访客 |
| **零失败限速、零锁定、零审计日志** | grep `attempt/lockout/rate_limit/fail_count` 全项目零命中；`logging` 三个主文件全零 |
| nginx 也没有限流 | [nginx.conf](deploy/nginx.conf) 无 `limit_req` / `limit_conn` |
| 应用已 public，这是唯一防线 | 之前为绕开 Streamlit 私有锁改的 |

4 位纯数字 = 1 万组合，名字已知、单轮 SHA256、无限重试。pin_hash 一旦从 Sheet 泄漏（协作者、误设公开链接）等于明文。

**修法方向**：PBKDF2/argon2 + 每账号失败计数与冷却 + PIN 位数下限提到 6；`delete_user`/`reset_pin` 加审计行。

---

# P1 · 会算错钱、或在关键时刻失败

## P1-1 ｜行情缓存无任何护栏，盘中未收盘价直接入库并进 git

**这是本仓库最宝贵的资产（7 个 csv，增量十年历史），目前是靠运气干净的。**

机制：

```python
dca_calculator.py:312-317   period1 = last_cached → 前沿日每次都重抓，按日期键覆盖
dca_calculator.py:292-298   落盘时 open("w") 整文件截断重写 + 排序
                            ↑ 非原子写（无 temp+rename）、无文件锁
dca_calculator.py:257       唯一校验：close is not None and close > 0
```

**污染已经发生过，在 git 历史里**——`GC_F.csv` 的 `2026-08-14` 这一天被写过三个不同的值：

| commit | 2026-08-14 的收盘价 |
|---|---|
| `c870ff4` | 4398.799805 |
| `9c00e2d` | 4405.299805 |
| `f06989f` / HEAD | 4380.399902 |

而 HEAD 上还有一行 `2026-08-17,4456.899902`——**今天，黄金期货正在盘中**，这是未收盘价。commit `f06989f` 的标题就写着"08-17 黄金盘中价增量刷新"。

**污染何时变永久**：只要那天还是文件里的最大日期，下次运行会被真收盘价覆盖（自愈一天）。**一旦有更晚的日期落库，前一天就再也不会被重新请求，脏值永久冻结。**

**缺失的校验（全部没有）**：收盘完成、`date <= today`、跳变阈值/离群、行数不减、陈旧上限。

**附带问题**：复权口径不一致——Chart 走原始 `quote.close`（206-208），yfinance 兜底用 `auto_adjust=True`（338）。SPY/QQQ 一旦拆股，两条路径的价格不兼容，且历史老行永不重抓 → 缓存出现永久断点。

**修法方向**：只持久化已收盘 K 线（前沿行标 provisional，不提交）；temp+rename+锁；加日期/跳变/非空三道闸；两条抓取路径统一复权口径。

## P1-2 ｜"今天"由进程时区决定 + 无重复记录防护 + 假日历 → 重复投

三个缺陷叠在一起：

- **无时区定义**：`date.today()` 遍布 [dca_calculator.py:302,856,860,910](scripts/dca_calculator.py#L302) 与 [app.py:1157,1235](app.py#L1157)。旧 Dockerfile 和 compose **都没设 `TZ`**，代码里无 `ZoneInfo`/`pytz`。Cloud 默认 UTC，用户在 UTC+8 → **北京时间每天 00:00–07:59，服务端的"今天"仍是前一天**。北京 8/18 00:30 记的观察会写成 `2026-08-17`。
- **写入无去重**：[app.py:1217,1251](app.py#L1217) 写入前不做任何 `(user, date, asset)` 检查，一天可以记任意多笔，每笔都吃本月预算。
- **交易日历不剔节假日**：[dca_calculator.py:142](scripts/dca_calculator.py#L142) 只有 `d.weekday() < 5`，docstring 自认"误差约 1 天/月"。后果：`paced_amount = 可用池 / 剩余交易日` 系统性偏小 3–5%，且**美股休市日照样出买入建议**。（讽刺的是 `backtest/*.py` 用的是从缓存推的真实交易日历，与线上引擎口径不一致。）

**还有一个静默的钱洞**：坏日期格式（如 `2026/08/17`）会被 [dca_calculator.py:464-467](scripts/dca_calculator.py#L464) 吞掉不进 XIRR，但 `invested_rmb` 照算；同时本月已投判定用 `startswith("2026-08")`（154-158）→ **这笔钱从本月预算里凭空释放，导致重复投**。

## P1-3 ｜汇率失败静默硬编码，四个常量相差 7%

| 值 | 位置 | 用途 |
|---|---|---|
| `7.20` | [dca_calculator.py:386](scripts/dca_calculator.py#L386) | USD/CNY 抓取失败的兜底 |
| `1.0` | [dca_calculator.py:398](scripts/dca_calculator.py#L398) | USDT/USD 兜底 |
| `6.73` | [app.py:1176,1178](app.py#L1176) | 记账页汇率默认值 |
| `6.7334` | [backtest/backtest_dca.py:25](backtest/backtest_dca.py#L25) | 回测固定汇率 |

`7.20` 与 `6.73` 差 **7%**。而且**兜底值不打标记**——输出 JSON 里没有任何字段区分"实时汇率"和"抓不到用的常量"，侧栏照常显示成实时值。整个组合的 RMB 估值可能默默错 1–3%。

## P1-4 ｜缓存陈旧无上限：Yahoo 挂三周仍照常出买入金额

- 有缓存时 Yahoo 异常只有 **1 级降级**：标 `data_source="cache_stale"` + warning，**根本不尝试 yfinance**（[dca_calculator.py:311-324](scripts/dca_calculator.py#L311)）
- **没有陈旧上限**——不存在"最后一根 K 线超过 N 天就拒绝出决策"的检查
- **更隐蔽**：增量抓取走 `allow_empty=True`（315），Yahoo 返回 200 但空 result 时**不算异常** → `data_source` 标成"增量成功"、**无 warning**、侧栏显示"全部正常"，实际一天没更新
- Yahoo **0 次重试 0 退避**（195-198 单次 urlopen），东财 3 次但**无退避**

## P1-5 ｜子进程超时紧贴上限：Yahoo 变慢（不必挂）就整页失败

8 个 Yahoo 请求**串行**、各 20s 超时 = 最坏 160s，而 `run_model` 的 subprocess 上限是 **180s**（[app.py:567](app.py#L567)）。超时不单独捕获，直接 `st.error` + `st.stop()`，**整页停住**。

## P1-6 ｜`deploy/` 那套 Docker 方案从来没成功跑过一次，却被标为"唯一事实源" —— ✅ 已解决（已删除）

CLAUDE.md:43 把 `deploy/DEPLOY.md` 标成"部署指南（唯一事实源）"。照它走连撞三道硬墙：

| 墙 | 证据 |
|---|---|
| **镜像里没有 `storage.py`** | [Dockerfile:19-21](deploy/Dockerfile#L19-L21) 只 COPY `app.py`/`scripts/`/`.streamlit/`，而 [app.py:22](app.py#L22) `import storage` → 启动即 `ModuleNotFoundError`，配 `restart: unless-stopped` = **无限崩溃重启** |
| **build context 错位** | [docker-compose.yml:26-27](deploy/docker-compose.yml#L26-L27) `context: .` + `dockerfile: deploy/Dockerfile`，而 DEPLOY.md 全程用 `-f deploy/docker-compose.yml` → 解析成 `deploy/deploy/Dockerfile`，构建立刻失败 |
| **挂载路径对不上** | DEPLOY.md:114 教你建 `data/me/`，compose 挂 `./data/user1` → 容器里读不到 config.json，`SystemExit` |

**铁证**：`deploy/` 的功能代码自 `574c7a7`（08-14 初始提交）起**一行未改**。真跑过的东西不可能零迭代。

顺带 `strategy/`、`backtest/`（被 `.dockerignore` 排除）、`data/market_history/` 都没进镜像 → Tab5/Tab6 在容器里是空的，每个新用户冷缓存起步。

## P1-7 ｜两套多用户实现互相抵消 —— ✅ 已解决（容器那套已删，只剩应用内登录）

| 实现 | 机制 |
|---|---|
| 容器隔离 + URL 路径 | 每用户一容器一 volume，nginx 按 `/user1/` 转发 |
| 应用内登录 + Sheets 行隔离 | 名字/PIN 门闸 + 所有表带 `user` 列 |

所有容器 COPY 的是**同一份 service account、指向同一个表格** → 容器隔离变成纯废重量；nginx 路径与 Sheets 用户名之间毫无绑定，**任何人从任意 `/xxx/` 路径都能登任何账号**。若从 git clone 部署（secrets 不在库里），门闸直接消失（P0-2），谁访问 `/zhangsan/` 谁就是 zhangsan。

**安全模型由"一个被 gitignore 排除的文件是否恰好在构建上下文里"决定，切换时零提示。**

## P1-8 ｜`setup_user.sh` 会用一个新用户炸掉所有用户 —— ✅ 已解决（脚本已删除）

[setup_user.sh:90](deploy/setup_user.sh#L90) 用 `sed -i -e "/^}$/r ..."` 插 location 块。文件里第一个独占一行的 `}` 是 **upstream 块的收尾**（[nginx.conf:12](deploy/nginx.conf#L12)），不是 server 块的（:58）→ location 被插到 server 块**外面** → nginx 报 `location directive is not allowed here` **完全起不来**。

这是运维里唯一频繁执行的动作，也是唯一没护栏的：无 `nginx -t`、无备份、无回滚，改完直接 `up -d --build`。服务名靠 `grep -oP 'dca-\w+'` 数出来，会数到注释模板 → 第一个新用户跳号成 `user3`，删过用户后撞名。临时文件用 `cat >>` 追加（:74），重跑写出重复 location。

## P1-9 ｜零测试、零 CI、依赖不钉版本

- **无 `tests/`、无 `.github/`**（零 CI）、无 pyproject/setup.cfg
- `requirements.txt` 全是无上界 `>=`（仅 `gspread<6` 有上界），**无 lock 文件**。本地实装 streamlit 1.61.1，requirements 写 `>=1.32.0` → **可复现性为零，上游一发版就可能把线上打挂**
- `Dockerfile:5` `FROM python:3.12-slim` 浮动 tag，无 digest
- **现有唯一的回归载体 `backtest/` 三个脚本全是坏的**：[backtest_dca.py:12,15](backtest/backtest_dca.py#L12) 硬编码 `C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca` —— 那个目录现在只剩 SKILL.md，`import dca_calculator` 直接失败。**而且这违反了 CLAUDE.md 自己写的"全项目零绝对路径"**
- 更糟：这三个脚本**自己重写了一遍决策链**（部署系数、月度池、月末释放），只复用 `asset_score`/`score_based_weights`/`xirr` → 即使修好路径，也测不到线上真跑的 `build_decision` + `monthly_budget_status`
- **好消息**：引擎大部分是纯函数、可 import 回归——`metrics_from_closes`(219)、`asset_score`(538)、`score_based_weights`(607)、`build_decision`(633)、`monthly_budget_status`(152)、`xirr`(410)、`load_cached_closes`(274)。改造成本很低
- **坏消息**：唯一的"固定输入→固定输出"入口 `main()`(815) 不封闭——必然联网、必然写缓存、输出嵌 `date.today()`

---

# P2 · 长期负债

| # | 问题 | 证据 |
|---|---|---|
| P2-1 | **零 TLS、无自有域名**：`server_name _;` + 只 `listen 80`，compose 只发布 `80:80`（443 没映射）；DEPLOY.md 的 certbot 段要求 nginx 在宿主机，但这里 nginx 在容器且 conf 是 `:ro` 挂载、无 ACME webroot → **照文档不可能签下证书**。线上只有平台子域 `dca365.streamlit.app` + ngrok free 域名，平台改策略无退路 | [nginx.conf:19-20](deploy/nginx.conf#L19-L20)、DEPLOY.md:183-191 |
| P2-2 | **可观测性全空**：零日志、零监控、零告警、零错误上报（`logging` 在三个主文件里 0 命中）。compose 无 `logging` 段 → json-file **无上限增长**会写满磁盘。healthcheck 有但**无消费者**（`depends_on` 没用 `condition: service_healthy`） | Dockerfile:28-29、docker-compose.yml |
| P2-3 | **备份不覆盖事实源**：旧 DEPLOY.md 那条 `tar -czf ... data/` 是手动无调度的，而线上真正的数据在 Google Sheets（storage.py:11 自称“唯一事实源”）。项目没有自动导出、命名快照、保留周期或恢复演练；平台版本历史只能算最后一道救命绳，不是项目自己的备份制度 | 旧 DEPLOY.md:264-271 |
| P2-4 | **Sheets 当 OLTP 的结构性代价**：每次 append = 读整表+写整表（写放大 O(n)，撞 60 读/分/用户 配额只是时间问题）；无主键无唯一约束；全字段 `.astype(str)`；`is_admin` 在缺 `role` 列时静默失效（`_read_ws` 缺列补空串）；8 秒缓存是**进程内全局**不是 per-session | storage.py:96,114-117,304-306 |
| P2-5 | **app.py 1984 行**，UI+认证+业务+数据混在一起（详见 `app-split-design.md`，6 刀方案已就绪） | app.py |
| P2-6 | **对用户的不实陈述**：[app.py:1983](app.py#L1983) 写着"所有记录和行情缓存都在本地 skill 目录，不上传任何地方"——真实持仓金额存在第三方 Google Sheets，且项目已迁出 skill 目录 | app.py:1983 |
| P2-7 | **无 API 层 / 前后端不分离**：Streamlit 单进程包住 UI+业务+数据。Claude Skill 那一路**绕过 app.py 直接 subprocess 调引擎** → 登录、预算覆盖、记账逻辑在 Skill 侧是缺失的，两个入口共用引擎但不共用业务层 | CLAUDE.md 架构图 |
| P2-8 | 死代码与孤儿：`verify_user`（storage.py:174，全项目零调用）、`append_csv`（app.py:583，定义后从未调用）、`OBS_CSV`（app.py:40，定义后从未使用）、`GLD.csv`（已不在抓取名单，冻结在 2026-08-10 却仍入库并被 CLAUDE.md 计作"7 个 csv"）；`run_model` 双跑（771-781 / 913-918）；README:16 指向已搬走的 `C:\Users\xiezhibo\start-dca-tunnel.bat`；解释器三套并存 | 各处 |

---

# 十二个面的体检表

| # | 面 | 状态 | 最重的一条 |
|---:|---|---|---|
| 1 | **身份认证与授权** | 🔴 | fail-open（P0-2）+ 4位PIN单轮SHA256无限速（P0-5） |
| 2 | **数据存储与一致性** | 🔴 | 一次读失败=全表覆写（P0-3） |
| 3 | **多租户隔离** | 🔴 | 跨用户串号，每天发生（P0-1） |
| 4 | **数据资产与备份** | 🔴 | Sheets 无备份、`.localbak` 是假保护（P0-3、P2-3） |
| 5 | **外部数据源可靠性** | 🟠 | 陈旧无上限、空响应不算异常（P1-4） |
| 6 | **计算正确性** | 🟠 | 无时区定义 + 无去重 + 假日历（P1-2）；四个汇率常量（P1-3） |
| 7 | **部署与环境** | 🟠 | Docker 那套是死物却被标唯一事实源（P1-6） |
| 8 | **域名/网络/TLS** | 🟠 | 零 TLS、无自有域名、certbot 文档不可执行（P2-1） |
| 9 | **密钥管理** | 🔴 | 私钥进镜像层（P0-4） |
| 10 | **可观测性** | 🔴 | 全空——线上炸了只能等用户告诉你（P2-2） |
| 11 | **质量工程** | 🟠 | 零测试零CI、依赖不钉、回测脚本硬编码死路径（P1-9） |
| 12 | **代码结构与文档一致性** | 🟡 | app.py 1984 行（P2-5）；文档多处不实（P2-6、P2-8） |

---

# 修复路线图

## 阶段一：止血（建议立刻，1–2 天）

**目标：让"会自己发生的坏事"停下来。不追求优雅，只追求不再丢数据、不再串号。**

1. **P0-1 跨用户串号** — `run_model` 缓存键加用户；每用户独立 `--base-dir`
2. **P0-3 数据覆写** — `_read_ws` 读失败抛异常而非返回空表；写前 Sheet 快照
3. **P0-2 认证 fail-open** — 启动期断言，secrets 缺失时 fail-closed
4. **P0-4 私钥进镜像** — `.dockerignore` 加 `.streamlit/secrets.toml`（一行）

这四条改动量都不大，但每一条都堵住一个正在滴血的口子。

## 阶段二：加固（1 周）

5. **P0-5** PIN 哈希换 PBKDF2/argon2 + 失败限速 + 审计行
6. **P1-1** 行情缓存三道闸（收盘完成 / date≤today / 跳变阈值）+ 原子写
7. **P1-2** 定死一个业务时区，全链路单一 today 来源；写入侧唯一约束；交易日历改从行情推
8. **P1-3** 汇率兜底打标记，四个常量收敛到一处
9. **P1-4** 加陈旧上限；空响应算异常
10. **P2-2** 最小可观测性：结构化日志 + 一个外部 uptime 探针

## 阶段三：地基（2 周）

11. **P1-9** 依赖钉版本 + lock；建 `tests/` 把 7 个纯函数罩住；修 `backtest/` 的死路径；加最小 CI
12. **P1-6/7/8** 部署路径**二选一**：要么把 Docker 那套真正构建验证一次，要么在 DEPLOY.md 首行标死"未验证"并明确 Cloud 是唯一生产路径
13. **P2-1** 自有域名 + TLS
14. **P2-6** 修正对用户的隐私陈述

## 阶段四：重构（原来的"第 2 件"）

15. **P2-5** app.py 拆分——`app-split-design.md` 里那 6 刀方案原样可用

**为什么拆分放最后**：拆分不修任何一个 P0/P1，但会**移动**所有相关代码。先修后拆，修的时候改动面小、定位准；先拆后修，等于在刚搬完家的房子里找漏水点。

---

# 待你拍板

1. **阶段一那四条，现在就动吗？** 都是小改动，但都在核心路径上。
2. **部署路径要不要收敛？** 目前四套并行（Cloud 在用 / ngrok 临时 / 本机直跑 / Docker 死物）。我建议只留 Cloud + 本机，Docker 那套要么修要么标死。
3. **`app-split-design.md` 降级到阶段四，认吗？**
4. **P0-1 的修法**：缓存键带用户是必须的；每用户独立目录在 Cloud 上要改 `BASE` 的推导方式——你希望走"Cloud 单实例多目录"还是"干脆不在本地落 CSV、让引擎直接读 Sheets"？后者改动大但一次性解决共享文件问题。

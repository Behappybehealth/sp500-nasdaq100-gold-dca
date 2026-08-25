# CLAUDE.md — 动态定投决策台

给 AI 编程助手的项目上下文。修改代码前先读本文件。

---

## 项目定位

标普500 / 纳指100 / 黄金三资产**动态定投决策系统**。两个使用入口：

| 入口 | 文件 | 说明 |
|---|---|---|
| **Web 决策台** | `app.py` | Streamlit 网页，多用户，云端存储 |
| **Claude Skill** | `X:\coding\skills\projects\sp500-nasdaq100-gold-dca\SKILL.md` | 对话式每日建议 |

两个入口共用同一套计算引擎和数据。

---

## 技术栈

**运行时与框架**
- **Python 3.14.4**（本机 `.venv` 实装）：全项目唯一解释器，命令一律走 `.venv/Scripts/python.exe`
- **Streamlit 1.61.1**：执行模型是「每次交互整个脚本从头重跑」——理解本项目的代码组织方式，先理解这个前提。`st.session_state` 每浏览器标签页一份；`st.cache_data` 全进程共享，缓存键必须含用户名，否则跨用户串号
- 前端不手写 HTML/JS；定制样式靠注入 CSS（`src/ui/styles.py`）

**数据与存储**
- **pandas 3.0.5 + numpy 2.5.2**：pandas 3 与 2 有不兼容改动，照搬网络示例前先核对版本
- **Google Sheets**（gspread 5.12.4）：多用户模式的唯一事实源；只能整表读、整表写——读失败抛错拒写，写前快照 `_bak`；每日快照备份走 Apps Script（`deploy/backup/Code.gs`，部署与演练见 DEPLOY.md §6）
- **本地 CSV 回退**：无 GCP 凭据时自动降级单机；云端模式每用户落盘缓存 `data/users/<user>/`

**行情数据**
- **Yahoo Chart v8**（`urllib` 直连，20s×3 次尝试带退避）→ **yfinance 1.6.0** 兜底；**东方财富 push2**（`curl` 子进程）供 XAU/BTC 实时价：三者皆非官方接口、无 key、无额度保证
- 口径已钉死 **raw**：Chart 用原始 close，yfinance 兜底 `auto_adjust=False`。**兜底结果只进内存不落库**（落库单一由 Chart 路径负责）——改抓价链时必须维持这条，否则库内会出现复权断点
- **两个落点、一条界线**：已收盘定稿值归 `data/market_history/*.csv`（`save_cached_closes` 剔除 `date >= utc_today()`）；当日未收盘值归 `data/market_live.json`（`split_live_bars` 只收 `date >= utc_today()`）。加载时 `merge_live_bars` 合并，**csv 有该日期则 csv 优先**——定稿值一落库自动顶掉临时值，无需清理逻辑。落库日界用 `utc_today()`、业务日界用 `biz_today()`，两者别混
- **每次重抓最近 5 天**（`_REFETCH_LOOKBACK_DAYS`）：`period1` 从「库内最后一天 − 5」起，请求数不增（同一个 Chart 调用 range 大一点）。数据源事后回填 null 空洞、修正错值都会自动追平；5 天之外的存量差异接受为既有事实
- **决策必须用得到实时价才放行**：主闸看本次有没有拿到 `regularMarketPrice`（`latest_source == "quote"`），拿不到即降级——旧收盘价冒充实时价是最危险的静默失败。副闸兜底死标的：K 线落后 > 10 天（`_MAX_STALE_DAYS`）仍拦。降级 = 不出金额、只展示持仓（`decision.degraded` + `decision.freshness`）

**认证**
- 自写「名字 + PIN」：PBKDF2-HMAC-SHA256（20 万迭代 + 每账号随机盐），连续失败 5 次锁 15 分钟；fail-closed——secrets 缺失/损坏即拒启动，仅显式 `DCA_AUTH_MODE=local` 进单机模式

**计算引擎**
- 独立脚本 `scripts/dca_calculator.py` + **subprocess 隔离**：UI 与计算零共享内存，只经命令行参数与 stdout JSON 通信，是本项目最干净的边界
- 行情快照 `data/quote_snapshot.json`（TTL 600s）复用抓价结果，TTL 内重跑近即时
- 8 个外部请求（6 标的 + 2 汇率）**并发同波**：总耗时取最大值而非求和，subprocess 180s 上限留足余量

**部署与外发**
- **Streamlit Community Cloud**：推 `main` 自动重新部署；**容器时区 UTC**——业务"今天"一律走 `biz_today()`（Asia/Shanghai 固定 UTC+8，`src/dates.py` 与引擎 `dca_calculator.py` 双实现同规则、必须同改），禁止裸 `date.today()`
- **ngrok 固定域名**：本机临时外发；`deploy/start-dca-tunnel.bat` 只能写 ASCII

**运行日志**
- **标准库 `logging`，零第三方**：配置集中 `src/obs.py`（`setup_logging()` 幂等，`app.py:31` 启动调一次，**必须在 `storage.init` 之前**），落点 stderr（Cloud 日志面板唯一可见处）+ `logs/dca.log`（1 MB × 3 轮转）
- **各模块自己 `logging.getLogger("dca.<频道>")`**，频道名写死不用 `__name__`——`storage.py` 的 `__name__` 是 `"storage"`，不在 `dca` 子树下，用它就只能去配 root logger，把 gspread/urllib3 噪声全引进来；这也让数据层不必反向 `import src`
- **只记失败与降级，绝不记 PIN/哈希/盐**（`tests/test_obs.py` 用 AST 扫全部调用点做断言，不靠 review）。埋点只加在**已有**异常分支上，不新造控制流；`except` 一律 `as e` 把原始异常带进日志——丢掉 `e` 就分不清凭据过期/配额撞满/网络抖动
- 详设与边界（含"零告警"这条已知边界）见 `docs/ARCHITECTURE-DETAIL.md` §12

**工程工具**
- **pytest 9.1.1**：回归套件只收 `tests/`，离线由 `conftest` 里 autouse 的 `_deny_network` 强制（socket/DNS/子进程四口全拦、回环放行），不靠各用例自觉；同处 autouse 的 `_quarantine_logging` 预占 `dca` logger 槽位，防 AppTest 把测试假异常写进工作树 `logs/dca.log`；Streamlit 整页冒烟用 `streamlit.testing.v1.AppTest`
- **GitHub Actions**：push/PR 全分支触发；三道门禁 ruff → mypy → pytest；Win/3.14 + Linux/3.12 双腿；junit XML 汇总到 Summary + artifact 留档 90 天；不依赖 secrets
- **依赖两份分工**：`requirements.txt` 是 Cloud 可安装范围（全部有上界），`requirements-dev.lock` 是 Windows/Python 3.14 开发机全量精确锁定
- **ruff + mypy** 均进 CI 强制（配置 `ruff.toml` + `mypy.ini`）；**git** + GitHub **公开**仓库（`Behappybehealth/sp500-nasdaq100-gold-dca`，用户有意设置）——**别假定它是私有的**：仓内文档与策略口径全部对外可见，写文档时按公开处理；判定用不带凭据的 `api.github.com/users/<账号>/repos`（只返回 public 仓）
---

## 目录结构与架构

→ **见 [`STRUCTURE.md`](STRUCTURE.md)**（依赖关系图 + 架构一览 + 英文/中文文件树，唯一事实源）。

---

## 已知技术债

清单不在本文维护——**问题的唯一事实源是 `docs/BUGLIST.md`**（含等级、状态、确认记录与验证结果）。本文列表曾与台账双源并行，已于 2026-08-20 收编：最后一条「状态管理分散」立为 `BUG-032`。

---

## 本地开发

```bash
# 启动 Web
cd X:/coding/projects/sp500-nasdaq100-gold-dca
.venv/Scripts/streamlit run app.py

# 跑全量离线回归（不得抓 Yahoo / 东财，不得连接真实 Google Sheets）
.venv/Scripts/python.exe -m pytest

# 单跑计算引擎（不带 --amount 则自动决定金额）
.venv/Scripts/python.exe scripts/dca_calculator.py --base-dir .
.venv/Scripts/python.exe scripts/dca_calculator.py --amount 5000 --base-dir .
```

`--base-dir` 指定数据目录，多用户部署时每人一个目录。

---

## 改代码注意事项

1. **不要把新逻辑继续堆进 app.py。** 按上面的拆分方案放到对应模块。
2. **计算逻辑改 `dca_calculator.py`**，不要在 app.py 里重算。
3. **数据读写走 `storage.py`**，不要直接操作 CSV。
4. **行情缓存是增量的**（`data/market_history/*.csv`），删掉某个 csv 会触发全量重建。
5. **数据文件不入库**（transactions / observations / budget_overrides / secrets），改动它们不需要 commit。
6. **`deploy/bin/` 不入库但必须留在项目内**，隧道脚本靠 `%~dp0bin\ngrok.exe` 相对定位找它。
7. **`deploy/start-dca-tunnel.bat` 只能写 ASCII**——cmd 按 OEM 码页（936）读批处理，UTF-8 中文注释会被当乱码命令执行。中文说明写进 `deploy/DEPLOY.md`。
8. **全项目零绝对路径**，保持这个性质，搬目录才不会断。归档回测脚本也必须用 `Path(__file__)` 相对定位。
9. **提交信息用 Conventional Commits**（`feat:` / `fix:` / `refactor:` / `chore:`）。
10. **动手前先读 `docs/ARCHITECTURE.md` 与 `docs/BUGLIST.md`** —— 前者是顶层架构唯一事实源（改实现细节另读 `docs/ARCHITECTURE-DETAIL.md`），后者是问题唯一事实源。`BUGLIST.md` 中每条问题必须先完成“1 对 1 确认修复路径”并回填确认记录，才允许修改真实逻辑；修复后必须回填实际改动、修复日期和真实验证结果。
11. **行为变更的 commit 必须同期核对相关活文档** —— 活/冻清单见 `docs/ARCHITECTURE.md` §9。活文档头部标 `【活·更新时机：…】`；`docs/plans/` 与 `backtest/` 的结果/报告是冻结产物，只增不回改。归档 `.py` 脚本允许做不改变历史结果含义的可移植性维护。
12. **每个 commit 同期在 `CHANGELOG.md` 追加一行**（`HH:MM:SS [类型] 一句话（hash；关联编号）`，按日期分组新在上、组内按时刻新在上）——这是全量改动的人读版流水；架构级变更另记 ARCHITECTURE 变更记录、问题生命周期另记 BUGLIST，三处粒度不同不重复。**时刻取自 git commit 时间，不手写**：`.venv/Scripts/python.exe scripts/changelog.py add <hash>` 生成行草稿，收尾跑 `--check` 校验每个 commit 都有行且时刻正确（手滑漏行/错时刻会被它拦下）。尾随约定：commit 自身那行由下一个 commit 携带入库。
13. **测试必须离线且用虚构数据。** 测试不得访问 Yahoo / 东财 / Google Sheets，不得把真实持仓或成本写进 fixture；storage 与 AppTest 每条路径都要显式 patch `sheets_enabled` 或强制 local，防本机真实 `secrets.toml` 被 pytest 读到后误写生产表。`conftest` 的 `_deny_network` 是兜底不是替代——它抓的是漏 patch，命中即 `NetworkUseInTests`；改守卫必须同步跑 `tests/test_offline_guard.py`。
14. **session_state 键一律走 `src/state.py` 登记表**：新增/删除键先改登记表（常量 + 归属链 + 生命周期注释），业务代码写裸字面量会被 `tests/test_state.py` 拦下；触发云端重同步调 `invalidate_sync()`，别直接 `pop`。

---

## 相关规范

遵循 `coding-standards` skill（通用研发规范）和 `self-check` skill（自查机制）。

---

## 数据口径

- **交易本位：** USDT（标识 "U"）
- **代码：** 标普500 → `SPY`，纳指100 → `QQQ`，黄金 → `XAUT`
- **估值：** SPY/QQQ 用 Yahoo 实时价 × USD/CNY；XAUT 用 `XAUT-USD` × U/CNY
- **月度预算：** 默认 30000 RMB，可按月覆盖（`budget_overrides.json`）
- **中性权重：** SP500 35% / NDX100 45% / 黄金 20%

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

## 目录结构

```
sp500-nasdaq100-gold-dca/
├── app.py                    # Streamlit 主程序（⚠️ 1163 行，拆分进行中：BUG-020 已落 3/7 刀）
├── storage.py                # 存储层：Google Sheets 优先，本地 CSV 回退（594 行；含写前快照、PBKDF2 认证）
├── src/                      # app.py 拆分新家（BUG-020，7 刀方案逐刀外搬中）
│   ├── context.py            # 启动上下文：Paths / Decision / build_paths（73 行；code_dir 按 parent.parent 定位）
│   ├── services/             # 服务层：model.py 模型调用（45）/ quotes.py 行情抓取（87）/ curves.py 曲线数据（85）
│   └── ui/                   # 样式与遮罩：styles.py 全局 CSS / overlays.py 三遮罩（刀 3）
├── CHANGELOG.md              # 改动日志：每个 commit 一行带时刻（人读版流水，见第 12 条；scripts/changelog.py 维护）
├── start-app.bat             # 本机双击启动 Streamlit
├── logs/                     # 运行日志约定落点（*.log 不入库；Cloud 容器重启即失，实现见 BUG-017）
├── scripts/
│   ├── dca_calculator.py     # 计算引擎（938 行，独立可运行，输出 JSON；--user 读 data/users/<user>/）
│   └── changelog.py          # CHANGELOG 维护：add <hash> 生成带时刻的行，--check 校验全覆盖
├── data/
│   ├── config.json           # 策略参数与资产定义
│   ├── budget_overrides.json # 月度预算覆盖（不入库）
│   ├── transactions.csv      # 成交记录（不入库；仅单机模式用，云端模式见 users/）
│   ├── observations.csv      # 跳过/观察记录（不入库；同上）
│   ├── users/                # 云端模式每用户落盘缓存（不入库，sync_local 生成，覆盖前轮转留底 10 份）
│   └── market_history/       # 行情缓存（date,close 两列，增量更新，6 个 csv）
├── strategy/
│   └── core-strategy.md      # 策略说明唯一事实源（Tab6 启动时读它渲染，改文档即改页面）
├── backtest/                 # 一次性回测脚本 + 结果（2026-08-11 跑完，非运行时依赖）
│   ├── backtest_dca.py / backtest_single.py / backtest_compare3.py  # ⚠️ 硬编码旧绝对路径，现在跑不起来
│   └── results*.json / results.md / compare3.md   # Tab5「回测结果」读这里
├── docs/
│   ├── README.md             # 文档门户：全部说明文件的索引（活/冻标注、读者、更新时机）
│   ├── ARCHITECTURE.md       # 顶层架构唯一事实源（概要版，仅顶层变动同期更新）
│   ├── ARCHITECTURE-DETAIL.md # 架构详设：实现细节/设计动机/踩坑/耦合实测（行为变更同期更新）
│   ├── BUGLIST.md            # 问题唯一事实源（逐条确认后才可修复）
│   └── plans/                # 计划、设计与历史审计快照
├── deploy/                   # 部署与外发（Docker 那套已于 2026-08-17 删除，见 DEPLOY.md 第 5 节）
│   ├── DEPLOY.md             # 部署指南：Cloud（生产）/ 本机 / ngrok
│   ├── start-dca-tunnel.bat  # ngrok 固定域名外发（⚠️ 只能写 ASCII，见 DEPLOY.md）
│   └── bin/                  # ngrok.exe（33 MB，随项目走但不入库，删了只能重下）
└── .streamlit/
    ├── config.toml           # 主题配置
    └── secrets.toml          # GCP 凭据（不入库）
```

---

## 架构：三层 + 一个边界

```
app.py（UI + 业务逻辑，耦合较紧）
   │
   ├── import ──────→ src/services/（模型调用 / 行情抓取 / 曲线数据；BUG-020 刀 2 已落，
   │                       函数显式收 src/context.Paths，不读 app.py 模块级全局）
   │
   ├── subprocess ──→ scripts/dca_calculator.py（纯计算，完全独立）
   │                       └── 读 data/*.csv + data/config.json
   │                       └── 抓 Yahoo Chart 行情（带缓存增量）
   │                       └── 输出 JSON（含 wide_table_markdown）
   │
   └── import ──────→ storage.py（数据层）
                           └── Google Sheets（多用户，唯一事实源）
                           └── 本地 CSV（无 secrets 时回退）
```

**关键设计：** `dca_calculator.py` 通过 subprocess 调用，与 UI 完全隔离。改计算逻辑不会影响 UI，反之亦然。

---

## 已知技术债

| 问题 | 现状 | 计划 |
|---|---|---|
| **app.py 过长** | 1163 行（原 1559；服务层 + CSS/遮罩已搬 `src/services/`、`src/ui/`），认证 + 侧栏 + 6 tab 仍混在一起 | 7 刀拆分中（BUG-020）：已落 3 刀，余 5 个只读 tab → 记账 tab → 侧栏 → 认证收口 |
| 状态管理分散 | `st.session_state` 多处读写 | 集中管理 |

**拆分方案（草案，动手前需重新核对行数与依赖）：**

```
src/
├── ui/
│   ├── styles.py          # CSS（~180 行）
│   ├── auth.py            # 登录门闸 + 遮罩（~200 行）
│   ├── sidebar.py         # 侧边栏：行情/预算/汇率（~260 行）
│   └── components.py      # loading / quote 卡片
├── tabs/
│   ├── today.py           # Tab1 今日模拟
│   ├── holdings.py        # Tab2 持仓与曲线
│   ├── records.py         # Tab3 记账
│   ├── history.py         # Tab4 历史
│   ├── backtest.py        # Tab5 回测结果
│   └── strategy_doc.py    # Tab6 策略文档
└── services/              # ✅ 刀 2 已落（实装名为 model.py 模型调用 / quotes.py 行情 / curves.py 曲线）
```

---

## 本地开发

```bash
# 启动 Web
cd X:/coding/projects/sp500-nasdaq100-gold-dca
.venv/Scripts/streamlit run app.py

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
8. **全项目零绝对路径**，保持这个性质，搬目录才不会断。（⚠️ `backtest/*.py` 三个脚本目前违反这条，写死了已失效的 `~/.claude/skills/...`，待修）
9. **提交信息用 Conventional Commits**（`feat:` / `fix:` / `refactor:` / `chore:`）。
10. **动手前先读 `docs/ARCHITECTURE.md` 与 `docs/BUGLIST.md`** —— 前者是顶层架构唯一事实源（改实现细节另读 `docs/ARCHITECTURE-DETAIL.md`），后者是问题唯一事实源。`BUGLIST.md` 中每条问题必须先完成“1 对 1 确认修复路径”并回填确认记录，才允许修改真实逻辑；修复后必须回填实际改动、修复日期和真实验证结果。
11. **行为变更的 commit 必须同期核对相关活文档** —— 活/冻清单见 `docs/README.md`（文档门户）。活文档头部标 `【活·更新时机：…】`，冻文档（`docs/plans/`、`backtest/`）标 `【冻】`、只增不改、不回改。
12. **每个 commit 同期在 `CHANGELOG.md` 追加一行**（`HH:MM:SS [类型] 一句话（hash；关联编号）`，按日期分组新在上、组内按时刻新在上）——这是全量改动的人读版流水；架构级变更另记 ARCHITECTURE 变更记录、问题生命周期另记 BUGLIST，三处粒度不同不重复。**时刻取自 git commit 时间，不手写**：`.venv/Scripts/python.exe scripts/changelog.py add <hash>` 生成行草稿，收尾跑 `--check` 校验每个 commit 都有行且时刻正确（手滑漏行/错时刻会被它拦下）。尾随约定：commit 自身那行由下一个 commit 携带入库。

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

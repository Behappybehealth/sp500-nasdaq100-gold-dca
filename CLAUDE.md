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
├── app.py                    # Streamlit 主程序（⚠️ 1985 行，待拆分）
├── storage.py                # 存储层：Google Sheets 优先，本地 CSV 回退
├── scripts/
│   └── dca_calculator.py     # 计算引擎（独立可运行，输出 JSON）
├── data/
│   ├── config.json           # 策略参数与资产定义
│   ├── budget_overrides.json # 月度预算覆盖（不入库）
│   ├── transactions.csv      # 成交记录（不入库）
│   ├── observations.csv      # 跳过/观察记录（不入库）
│   └── market_history/       # 行情缓存（date,close 两列，增量更新）
├── strategy/
│   └── core-strategy.md      # 策略详细文档
├── deploy/                   # Docker + nginx 多用户部署
└── .streamlit/
    ├── config.toml           # 主题配置
    └── secrets.toml          # GCP 凭据（不入库）
```

---

## 架构：三层 + 一个边界

```
app.py（UI + 业务逻辑，耦合较紧）
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
| **app.py 过长** | 1985 行，UI + 认证 + 业务 + 数据抓取混在一起 | 拆成 `src/ui/`、`src/tabs/`、`src/services/` |
| 状态管理分散 | `st.session_state` 多处读写 | 集中管理 |

**拆分方案（待执行）：**

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
└── services/
    ├── model_runner.py    # 调用 dca_calculator 子进程
    ├── market_quote.py    # 东财 XAU / BTC 行情
    └── recorder.py        # 交易记录写入
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
6. **提交信息用 Conventional Commits**（`feat:` / `fix:` / `refactor:` / `chore:`）。

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

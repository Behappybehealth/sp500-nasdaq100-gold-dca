# 模拟定投决策台（Streamlit）

[![CI](https://github.com/Behappybehealth/sp500-nasdaq100-gold-dca/actions/workflows/ci.yml/badge.svg)](https://github.com/Behappybehealth/sp500-nasdaq100-gold-dca/actions/workflows/ci.yml)

🌐 **在线访问**：<https://dca365.streamlit.app/>

面向小团队/家庭试用的 DCA 定投回测与决策工具。标普500 / 纳指100 / 黄金三资产动态定投，数据层以 Google Sheets 为事实源。

> 合规提示：本项目是回测/计算器工具，不构成投资建议。

---

📖 # Project Structure — sp500-nasdaq100-gold-dca

> S&P 500 / Nasdaq 100 / Gold dynamic DCA decision system.
> Two entry points (Streamlit web app + Claude Skill) share one calculation engine and one storage layer.
> Last updated: 2026-08-24 · Total source: ~7,500 lines (excluding .venv, data, logs, backtest JSONs)

快速入口：
| 需要看什么 | 去哪 |
|---|---|
| 项目结构与依赖关系 | [STRUCTURE.md](STRUCTURE.md) |
| 架构设计与数据流 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 部署（Cloud / 本机 / ngrok） | [deploy/DEPLOY.md](deploy/DEPLOY.md) |
| 改动流水 | [CHANGELOG.md](CHANGELOG.md) |
| 问题台账 | [docs/BUGLIST.md](docs/BUGLIST.md) |

---

## 核心架构链路 · 目录结构与数量

> 核心链路 = **三层 + 一个边界**（[ARCHITECTURE.md §3–§5](docs/ARCHITECTURE.md)）：
> `app.py`（装配）─subprocess→ `scripts/dca_calculator.py`（纯计算）─import→ `storage.py`（数据层）；
> 认证 / 决策 / 记账三条业务链路全部落在 `src/`。

```
sp500-nasdaq100-gold-dca/
├── app.py                  #  70行  装配入口：import→build_paths→setup_logging→storage.init→认证→侧栏→6 tab
├── storage.py              # 666行  存储层：Sheets/CSV、PBKDF2 认证、写前快照
│
├── src/                    # 业务层 · 22 文件 / 2164 行 —— 三条链路全在这
│   ├── context.py · dates.py · obs.py · state.py · market_cache.py
│   ├── services/           # 决策链：model(子进程调引擎) · quotes(行情) · curves(曲线)
│   ├── ui/                 # 认证链+决策链UI：auth(门闸) · sidebar(模型执行点) · styles · overlays
│   └── tabs/              # 记账链+展示：today · holdings · records(写) · history(读) · backtest · strategy_doc
│
├── scripts/                # 计算引擎 · 8 文件 / 1811 行 —— 线性 DAG，零反向依赖
│   ├── dca_calculator.py   # 240行 入口薄壳：main() + re-export（subprocess 边界另一侧）
│   ├── dca_types → dca_market → dca_portfolio → dca_scoring → dca_table   # 5 兄弟模块 · 1506 行
│   └── dca_action.py · changelog.py   # Skill CLI · CHANGELOG 工具
│
└── data/                   # 引擎唯一数据源
    ├── config.json         # 策略参数（权重/区间/评分系数）
    ├── users/<用户>/       # 云端模式每用户落盘缓存（sync_local 生成）
    ├── market_history/     # 已收盘定稿价 · 6 CSV（_GSPC/SPY/_NDX/QQQ/GC_F/XAUT_USD）
    └── quote_snapshot.json # 行情快照（TTL 600s，重跑近即时）
```

| 层 | 顶层单元 | 文件数 | 行数 | 核心链路角色 |
|---|---|---:|---:|---|
| 装配层 | `app.py` | 1 | 70 | 入口，纯装配 |
| 数据层 | `storage.py` | 1 | 666 | Sheets/CSV 读写、PBKDF2 认证、写前快照 |
| 业务层 | `src/` | 22 | 2,164 | 认证 / 决策 / 记账 三条链路 |
| 计算引擎 | `scripts/` | 8 | 1,811 | subprocess 隔离的纯计算（引擎 6 模块 1,506 行） |
| 数据目录 | `data/` | — | — | 引擎唯一数据源（不入库） |
| **合计** | **5 个顶层单元** | **32 .py** | **4,711** | — |

> 子进程边界（`app.py` ↔ `dca_calculator.py`）是本项目最干净的设计：改计算不影响 UI，反之亦然。
> 完整文件树与依赖关系图见 [STRUCTURE.md](STRUCTURE.md)。

---

## 全项目源代码行数构成

> 与上方「核心链路 4,711 行」对照：核心链路是全项目的子集。

| 口径 | 行数 | 构成 |
|---|---:|---|
| 核心链路（见上表） | 4,711 | app.py 70 + storage.py 666 + src/ 2,164 + scripts/ 1,811 |
| tests/ | 2,087 | 离线回归套件 |
| backtest/ | 659 | 归档的一次性回测脚本 |
| deploy/Code.gs | 201 | Apps Script 备份（.gs，非 .py） |
| **全项目 .py 合计** | **7,457** | （+ Code.gs 201 = 7,658） |

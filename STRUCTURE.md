# Project Structure — sp500-nasdaq100-gold-dca

> S&P 500 / Nasdaq 100 / Gold dynamic DCA decision system.
> Two entry points (Streamlit web app + Claude Skill) share one calculation engine and one storage layer.
> Last updated: 2026-08-24 · Total source: ~6,900 lines (excluding .venv, data, logs, backtest JSONs)

---

## 依赖关系图 / Dependency Graph

```
app.py (装配, 70行)
 ├→ storage.py          ──→ streamlit/pandas/gspread   ⚠ 数据层反向依赖UI框架
 ├→ src/context.py       ──→ (纯dataclass, 无依赖)       ✅
 ├→ src/obs.py           ──→ (纯logging, 无依赖)         ✅
 ├→ src/ui/auth.py       ──→ storage, state, overlays
 ├→ src/ui/sidebar.py    ──→ storage, services.model, services.quotes   (协调器)
 ├→ src/ui/styles.py     ──→ (纯CSS)                     ✅
 ├→ src/tabs/today.py    ──→ services.model
 ├→ src/tabs/records.py  ──→ storage, state
 ├→ src/tabs/holdings.py ──→ services.curves
 ├→ src/tabs/backtest.py ──→ services.curves
 ├→ src/services/model.py──→ subprocess → dca_calculator  ✅ 子进程隔离
 ├→ src/services/quotes.py──→ context (⚠ @st.cache_data 绑定Streamlit)
 └→ src/services/curves.py──→ src/market_cache (无sys.path hack)  ✅

scripts/dca_calculator.py (入口240行 + 5兄弟模块) ──→ stdlib only  ✅ 零反向依赖
scripts/dca_action.py     ──→ dca_calculator, storage  (CLI入口)
```

---

## Architecture at a Glance / 架构一览

```
                    ┌──────────────────────────────────────────┐
   Browser (user)   │  Streamlit Community Cloud               │
        ←─────────→ │  one Python process serves all users     │
                    │                                          │
                    │    app.py (70L, pure assembly)           │
                    │    auth / sidebar / 6 tabs all in src/   │
                    └───┬────────────────────────┬─────────────┘
                        │ subprocess             │ import
                        │ (isolated)             │
                        ▼                        ▼
          ┌─────────────────────────┐   ┌──────────────────┐
          │ scripts/dca_calculator  │   │   storage.py     │
          │ Entry (240L) + 5 mods   │   │  Storage (654L)  │
          │                         │   │                  │
          │ reads data/config.json  │◄──┤  Google Sheets    │
          │ reads data/users/<u>/   │   │  or local CSV     │
          │ reads market_history/   │   └────────┬─────────┘
          │ fetches Yahoo / FX      │            │
          │ outputs JSON to stdout   │            ▼
          └───────────┬─────────────┘   ┌──────────────────┐
                      │                 │  Google Sheets    │
                      ▼                 │  4 tables + _bak  │
          ┌────────────────────┐        └──────────────────┘
          │ Yahoo Chart v8     │
          │ yfinance fallback  │
          │ (no API key)        │
          └────────────────────┘
```

**Three layers + one boundary**: `app.py` (UI + business logic) → subprocess-isolated `dca_calculator.py` (pure computation, split into 6 modules) → `storage.py` (data layer, imported directly).

**Engine module dependency DAG** (linear, no cycles):

```
dca_types ──→ dca_market ──→ dca_portfolio ──→ dca_scoring ──→ dca_table ──→ dca_calculator (entry + re-exports)
```

| Metric | Value |
|---|---|
| Total source lines (excl. .venv/data/logs) | ~6,900 |
| Engine modules | 6 files, 1,506 lines |
| Web app modules (src/) | 22 files, 2,164 lines |
| Tests | 9 files, 2,087 lines, 130 tests |
| Bug ledger | 35 bugs: 32 ✅ fixed + 3 ⚪ won't-fix |
| Dependencies | 6 direct (streamlit, pandas, numpy, gspread, yfinance, google-auth) |
| External APIs | Yahoo Finance (no key), Google Sheets (GCP service account) |
| CI | GitHub Actions, 2 legs (Win/3.14 lock + Linux/3.12 range), fully offline |

---

## English — File Tree

```
sp500-nasdaq100-gold-dca/
│
├── app.py                          # 70L  Streamlit entry — pure assembly
├── storage.py                      # 654L Storage layer — Sheets/CSV, PBKDF2 auth, write-before-snapshot
├── start-app.bat                   # 5L   Local Windows launcher
│
├── requirements.txt                # 17L  Cloud/Linux deps (6 direct, version-bounded)
├── requirements-dev.lock           # 82L  Windows/3.14 exact lock (full pip freeze)
├── pytest.ini                      # 7L   pytest config — collects tests/ only
├── .gitignore                      # 50L  Ignores .venv, logs/*.log, data/users/, secrets
├── .gitattributes                  # 6L   CRLF normalization
│
├── .github/workflows/ci.yml         # 47L  GitHub Actions — Win/3.14 + Linux/3.12, fully offline
│
├── .streamlit/
│   ├── config.toml                 # 8L   Theme colors
│   ├── secrets.toml                #      GCP service account (NOT in git)
│   └── secrets.toml.example        # 18L  Secrets template
│
├── scripts/                        # ── Calculation engine (6 mods, linear DAG, no cycles) ──
│   ├── dca_calculator.py           # 240L Entry: main() + re-exports 52 symbols (thin shell)
│   ├── dca_types.py                # 211L Data structs, biz_today(), read_transactions/observations
│   ├── dca_market.py               # 524L Yahoo fetch, cache I/O, FX rates, quote snapshot (TTL 600s)
│   ├── dca_portfolio.py            # 131L xirr (Newton-Raphson), portfolio_summary (PnL, return rate)
│   ├── dca_scoring.py              # 247L asset_score, build_decision, market_freshness gate
│   ├── dca_table.py                 # 153L build_wide_rows (list[dict]), render_wide_table (markdown)
│   ├── dca_action.py               # 190L Claude Skill CLI: record tx/obs, override
│   └── changelog.py                # 115L CHANGELOG tool: add <hash> / --check
│
├── src/                            # ── Web app business layer ──
│   ├── context.py                  # 73L  Paths/Decision dataclass, build_paths()
│   ├── dates.py                    # 20L  biz_today() (Asia/Shanghai, mirrors engine)
│   ├── obs.py                      # 62L  setup_logging() — stderr + 1MB×3 rotating file
│   ├── state.py                    # 113L session_state key registry (11 constants + ALL_KEYS)
│   ├── market_cache.py             # 38L  Pure cache funcs for curves.py (no engine dep)
│   ├── services/
│   │   ├── model.py                # 61L  run_model() — subprocess → engine, @st.cache_data 900s
│   │   ├── quotes.py              # 101L fetch_xau_spot(), fetch_btc(paths) — sidebar cards
│   │   └── curves.py              # 97L  load_price_series(), portfolio_curve() — net value chart
│   ├── ui/
│   │   ├── styles.py               # 185L Global CSS injection
│   │   ├── overlays.py             # 59L  Three masks: auth, sync, loading
│   │   ├── sidebar.py              # 339L User mgmt, quote cards, amount form, model execution
│   │   └── auth.py                 # 365L require_user() — 3-stage state machine, fail-closed
│   └── tabs/
│       ├── today.py                # 99L  Tab1: today's suggestion + 3-tier plan
│       ├── holdings.py             # 78L  Tab2: portfolio summary, XIRR, curve
│       ├── records.py              # 169L Tab3: record tx/obs → confirm → append_row
│       ├── history.py              # 26L  Tab4: read-back tx/obs
│       ├── backtest.py             # 249L Tab5: 5-section static report (reads backtest/*.json)
│       └── strategy_doc.py         # 18L  Tab6: renders strategy/core-strategy.md
│
├── data/                           # ── Engine data directory ──
│   ├── config.json                 #      Strategy params: weights, ranges, scoring coefficients
│   ├── transactions.csv            #      Ledger (local mode; cloud uses users/<user>/)
│   ├── observations.csv            #      Skip/observation log
│   ├── budget_overrides.json       #      Monthly budget overrides
│   ├── fx_last.json                #      FX fallback (per-field fetched_at)
│   ├── market_live.json            #      Intraday unclosed bars (merge on load)
│   ├── quote_snapshot.json         #      Market snapshot (TTL 600s)
│   ├── xau_spot_last.json          #      XAU spot fallback
│   ├── btc_last.json               #      BTC fallback (stale_label annotation)
│   ├── users/                      #      Per-user local cache (sync_local generates)
│   └── market_history/             #      Closed-bar closes (6 CSVs: _GSPC, SPY, _NDX, QQQ, GC_F, XAUT_USD)
│
├── tests/                          # ── Offline regression suite (130 tests) ──
│   ├── conftest.py                 # 143L Autouse: _deny_network (socket/DNS/subprocess)
│   ├── test_engine.py              # 495L Engine pure-function tests (46)
│   ├── test_storage.py             # 297L Storage local + Sheets safe-path (25)
│   ├── test_smoke.py               # 454L AppTest full-page smoke (20)
│   ├── test_state.py               # 223L session_state key registry (17)
│   ├── test_obs.py                 # 213L Logging config (7)
│   ├── test_offline_guard.py       # 74L  Offline guard self-test (8)
│   ├── test_backup_script.py       # 117L Backup Code.gs invariants (5)
│   └── test_deprecated_api.py      # 71L  Deprecated API zero-residue (2)
│
├── deploy/                         # ── Deployment ──
│   ├── DEPLOY.md                   # 184L Three paths: Cloud (prod) / local / ngrok
│   ├── backup/Code.gs              # 201L Apps Script daily backup → Drive (30-day retention)
│   ├── start-dca-tunnel.bat        # 41L  ngrok launcher (ASCII only)
│   └── bin/ngrok.exe               # 33MB NOT in git
│
├── backtest/                       # ── One-time backtest products (not runtime) ──
│   ├── backtest_dca.py             # 235L Archived: DCA strategy backtest
│   ├── backtest_single.py          # 183L Archived: single-asset dynamic vs fixed
│   ├── backtest_compare3.py        # 241L Archived: 3-strategy comparison
│   ├── results_rolling.json        #      Frozen — Tab5 §3+4
│   ├── results_compare3.json       #      Frozen — Tab5 §1
│   └── results_single_compare.json #      Frozen — Tab5 §3
│
├── strategy/core-strategy.md       # 184L Strategy doc — single source of truth, Tab6 renders
│
├── docs/                           # ── Documentation ──
│   ├── README.md                   # 46L  Doc portal — index of all docs
│   ├── ARCHITECTURE.md             # 365L Top-level architecture (structure SOT)
│   ├── ARCHITECTURE-DETAIL.md      # 412L Architecture detail (implementation, pitfalls)
│   ├── BUGLIST.md                  # 2107L Bug ledger — 35 bugs (32✅ + 3⚪)
│   ├── CODE_REVIEW_2026-08-24.md   # 103L Architecture code review report
│   └── plans/                      #      Design docs + historical audits (6 files)
│
├── logs/                           # Runtime log — dca.log (1MB×3, *.log not in git)
├── CHANGELOG.md                    # Commit log — one line per commit, generated by changelog.py
├── CLAUDE.md                       # AI assistant project guide
├── README.md                       # Project readme
└── STRUCTURE.md                    # This file
```

---

## 中文 — File Tree

```
sp500-nasdaq100-gold-dca/
│
├── app.py                          # 70行  Streamlit主程序——纯装配层
├── storage.py                      # 654行 存储层——Sheets/CSV、PBKDF2认证、写前快照
├── start-app.bat                   # 5行   本机Windows双击启动
│
├── requirements.txt                # 17行  Cloud/Linux依赖（6个直接，带版本上下界）
├── requirements-dev.lock           # 82行  Windows/3.14精确锁定（完整pip freeze）
├── pytest.ini                      # 7行   pytest配置——只收tests/
├── .gitignore                      # 50行  忽略.venv、logs/*.log、data/users/、secrets
├── .gitattributes                  # 6行   CRLF规范化
│
├── .github/workflows/ci.yml         # 47行  GitHub Actions——Win/3.14 + Linux/3.12，全离线
│
├── .streamlit/
│   ├── config.toml                 # 8行   主题配色
│   ├── secrets.toml                #       GCP服务账号（不入库）
│   └── secrets.toml.example        # 18行  凭据模板
│
├── scripts/                        # ── 计算引擎（6模块，线性DAG，无循环）──
│   ├── dca_calculator.py           # 240行 入口：main() + re-export 52符号（薄壳）
│   ├── dca_types.py                # 211行 数据结构、biz_today()、read_transactions/observations
│   ├── dca_market.py               # 524行 Yahoo抓取、缓存I/O、汇率、快照（TTL 600秒）
│   ├── dca_portfolio.py            # 131行 xirr（牛顿迭代）、portfolio_summary（浮盈亏、收益率）
│   ├── dca_scoring.py              # 247行 asset_score、build_decision、market_freshness闸
│   ├── dca_table.py                 # 153行 build_wide_rows（list[dict]）、render_wide_table
│   ├── dca_action.py               # 190行 Claude Skill CLI：record tx/obs、override
│   └── changelog.py                # 115行 CHANGELOG工具：add <hash> / --check
│
├── src/                            # ── Web应用业务层 ──
│   ├── context.py                  # 73行  Paths/Decision dataclass、build_paths()
│   ├── dates.py                    # 20行  biz_today()（Asia/Shanghai，与引擎同规则双实现）
│   ├── obs.py                      # 62行  setup_logging()——stderr + 1MB×3轮转
│   ├── state.py                    # 113行 session_state键登记表（11常量 + ALL_KEYS）
│   ├── market_cache.py             # 38行  curves.py的纯缓存函数（不依赖引擎）
│   ├── services/
│   │   ├── model.py                # 61行  run_model()——子进程调引擎，@st.cache_data 900秒
│   │   ├── quotes.py              # 101行 fetch_xau_spot()、fetch_btc(paths)——侧边栏卡片
│   │   └── curves.py              # 97行  load_price_series()、portfolio_curve()——净值曲线
│   ├── ui/
│   │   ├── styles.py               # 185行 全局CSS注入
│   │   ├── overlays.py             # 59行  三个遮罩：认证、同步、加载
│   │   ├── sidebar.py              # 339行 用户管理、行情卡片、金额表单、模型执行点
│   │   └── auth.py                 # 365行 require_user()——三阶段状态机，fail-closed
│   └── tabs/
│       ├── today.py                # 99行  Tab1：今日建议 + 三档执行方案
│       ├── holdings.py             # 78行  Tab2：持仓汇总、XIRR、净值曲线
│       ├── records.py              # 169行 Tab3：回报成交/跳过 → 确认 → append_row
│       ├── history.py              # 26行  Tab4：回读 tx/obs
│       ├── backtest.py             # 249行 Tab5：5段静态报告（读backtest/*.json）
│       └── strategy_doc.py         # 18行  Tab6：渲染strategy/core-strategy.md
│
├── data/                           # ── 引擎数据目录 ──
│   ├── config.json                 #      策略参数：权重、区间、评分系数
│   ├── transactions.csv            #      成交记录（本地模式；云端用users/<user>/）
│   ├── observations.csv            #      跳过/观察记录
│   ├── budget_overrides.json       #      月度预算覆盖
│   ├── fx_last.json                #      汇率兜底（分字段fetched_at）
│   ├── market_live.json            #      当日未收盘K线（加载时merge）
│   ├── quote_snapshot.json         #      行情快照（TTL 600秒）
│   ├── xau_spot_last.json          #      XAU现货价兜底
│   ├── btc_last.json               #      BTC兜底（stale_label标注）
│   ├── users/                      #      每用户本地缓存（sync_local生成）
│   └── market_history/             #      已收盘收盘价（6个CSV：_GSPC, SPY, _NDX, QQQ, GC_F, XAUT_USD）
│
├── tests/                          # ── 离线回归套件（130条）──
│   ├── conftest.py                 # 143行 autouse：_deny_network（socket/DNS/子进程）
│   ├── test_engine.py              # 495行 引擎纯函数测试（46条）
│   ├── test_storage.py             # 297行 存储层本地+Sheets安全路径（25条）
│   ├── test_smoke.py               # 454行 AppTest整页冒烟（20条）
│   ├── test_state.py               # 223行 session_state键登记表（17条）
│   ├── test_obs.py                 # 213行 日志配置（7条）
│   ├── test_offline_guard.py       # 74行  拒网守卫自测（8条）
│   ├── test_backup_script.py       # 117行 备份Code.gs不变量（5条）
│   └── test_deprecated_api.py      # 71行  弃用API零残留（2条）
│
├── deploy/                         # ── 部署 ──
│   ├── DEPLOY.md                   # 184行 三条路径：Cloud（生产）/ 本机 / ngrok
│   ├── backup/Code.gs              # 201行 Apps Script每日备份→Drive（30天保留）
│   ├── start-dca-tunnel.bat        # 41行  ngrok启动器（仅ASCII）
│   └── bin/ngrok.exe               # 33MB 不入库
│
├── backtest/                       # ── 一次性回测产物（非运行时）──
│   ├── backtest_dca.py             # 235行 归档：DCA策略回测
│   ├── backtest_single.py          # 183行 归档：单品种动态vs固定
│   ├── backtest_compare3.py        # 241行 归档：三策略对比
│   ├── results_rolling.json        #      冻结——Tab5§3+4
│   ├── results_compare3.json       #      冻结——Tab5§1
│   └── results_single_compare.json  #      冻结——Tab5§3
│
├── strategy/core-strategy.md       # 184行 策略说明——唯一事实源，Tab6渲染
│
├── docs/                           # ── 文档 ──
│   ├── README.md                   # 46行  文档门户——全部说明文件索引
│   ├── ARCHITECTURE.md             # 365行 顶层架构（结构唯一事实源）
│   ├── ARCHITECTURE-DETAIL.md      # 412行 架构详设（实现、踩坑）
│   ├── BUGLIST.md                  # 2107行 问题台账——35条（32✅ + 3⚪）
│   ├── CODE_REVIEW_2026-08-24.md   # 103行 架构代码审查报告
│   └── plans/                      #      设计文档+历史审计（6个文件）
│
├── logs/                           # 运行日志——dca.log（1MB×3，*.log不入库）
├── CHANGELOG.md                    # 改动流水——每commit一行，changelog.py生成
├── CLAUDE.md                       # AI编程助手项目说明
├── README.md                       # 项目自述
└── STRUCTURE.md                    # 本文件
```

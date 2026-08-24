# Project Structure — sp500-nasdaq100-gold-dca

> S&P 500 / Nasdaq 100 / Gold dynamic DCA decision system.
> Two entry points (Streamlit web app + Claude Skill) share one calculation engine and one storage layer.
> Last updated: 2026-08-24 · Total source: ~6,900 lines (excluding .venv, data, logs, backtest JSONs)

---

## English

```
sp500-nasdaq100-gold-dca/
│
├── app.py                          # 70L  Streamlit entry point — pure assembly layer
│                                  #       import → build_paths → setup_logging → storage.init
│                                  #       → auth gate → sidebar → 6 tab render() calls
│
├── storage.py                      # 654L Storage layer — single source of truth for all data I/O
│                                  #       Google Sheets (primary) / local CSV (fallback)
│                                  #       PBKDF2 auth, write-before-snapshot, dedup, per-user isolation
│                                  #       19 public functions; build_tx_row / build_obs_row schema constructors
│
├── start-app.bat                   # 5L   Double-click launcher for local Windows
│
├── requirements.txt                # 17L  Cloud/Linux install range — 6 direct deps with version bounds
├── requirements-dev.lock           # 82L  Windows/Python 3.14 exact lock — full pip freeze for reproducibility
├── pytest.ini                      # 7L   pytest config — collects tests/ only, excludes backtest scripts
├── .gitignore                      # 50L  Ignores .venv, logs/*.log, data/users/, secrets, btc_last.json, data/data/
├── .gitattributes                  # 6L   CRLF normalization for Windows
├── .dockerignore                   # 16L  Docker safety default (currently unused; Docker files deleted)
│
├── .github/
│   └── workflows/
│       └── ci.yml                  # 47L  GitHub Actions — push main triggers Windows/3.14 + Linux/3.12 pytest
│
├── .streamlit/
│   ├── config.toml                 # 8L   Theme colors
│   ├── secrets.toml                #      GCP service account credentials (NOT in git)
│   └── secrets.toml.example        # 18L  Template for secrets
│
├── scripts/                        # ── Calculation engine (6 modules, linear DAG, no circular imports) ──
│   │
│   ├── dca_calculator.py           # 240L Entry point: main() + stdout/stderr reconfigure + re-exports
│   │                              #       Thin shell — all 52 public symbols re-exported from siblings
│   │                              #       `import dca_calculator as eng` works with zero caller changes
│   │                              #       CLI: --amount --base-dir --history-years --user --snapshot-ttl
│   │                              #       Output: JSON to stdout (18 top-level keys)
│   │
│   ├── dca_types.py                # 211L Data structures, utilities, ledger data loading
│   │                              #       DEFAULT_CONFIG, _BIZ_TZ, biz_today(), is_iso_date(), utc_today()
│   │                              #       Transaction, read_json, resolve_monthly_budget, as_float
│   │                              #       read_transactions, read_observations, read_last_observation
│   │                              #       trading_days_in_month, monthly_budget_status
│   │
│   ├── dca_market.py               # 524L Market data fetching, cache I/O, FX rates
│   │                              #       fetch_json (Yahoo Chart v8, 3-retry backoff)
│   │                              #       fetch_chart, metrics_from_closes, pairs_from_chart_result
│   │                              #       sanitize_symbol, cache_file_for, load_cached_closes
│   │                              #       close_at_or_before, save_cached_closes (3-guardrail write)
│   │                              #       load_market_live, save_market_live, split_live_bars, merge_live_bars
│   │                              #       _yfinance_closes (fallback, not persisted)
│   │                              #       get_symbol_history, fetch_history (ThreadPoolExecutor concurrent)
│   │                              #       fetch_usdcny, fetch_usdtusd, load_fx_last, save_fx_last, _fx_entry
│   │                              #       load_quote_snapshot, save_quote_snapshot (TTL 600s)
│   │                              #       market_symbol_for_asset
│   │
│   ├── dca_portfolio.py            # 131L Portfolio computation
│   │                              #       xnpv, xirr (Newton-Raphson XIRR)
│   │                              #       portfolio_summary (price proxy lookup, FX selection, PnL, return rate)
│   │
│   ├── dca_scoring.py              # 247L Scoring model and decision engine
│   │                              #       clip, DEFAULT_MODEL, asset_score
│   │                              #       level_label, neutral_weights, score_based_weights
│   │                              #       market_freshness (main gate: latest_source != "quote"; sub: >10 days)
│   │                              #       build_decision (score → deploy coefficient × rhythm coefficient → amount
│   │                              #                     → weight tilt → asset ratios)
│   │
│   ├── dca_table.py                # 153L Wide table rendering
│   │                              #       WIDE_TABLE_HEADER, _money, _pct, _num, _xirr_cell
│   │                              #       build_wide_rows (list[dict] structured intermediate)
│   │                              #       render_wide_table (markdown from build_wide_rows)
│   │                              #       asset_note
│   │
│   ├── dca_action.py               # 190L Business action CLI for Claude Skill
│   │                              #       `record tx` / `record obs` / `override` subcommands
│   │                              #       Shares storage.py with web app — same validation, same schema
│   │                              #       Auto-calculates shares from amount; auto sync_local after write
│   │
│   └── changelog.py                # 115L CHANGELOG maintenance tool
│                                  #       `add <hash>` — generates line from git commit time
│                                  #       `--check` — verifies all commits have lines with correct timestamps
│
├── src/                            # ── Web app business layer (app.py is pure assembly) ──
│   │
│   ├── __init__.py                 # 2L   Package marker
│   ├── context.py                  # 73L  Startup context: Paths dataclass, Decision dataclass, build_paths()
│   ├── dates.py                    # 20L  Business "today" — biz_today() (Asia/Shanghai, fixed UTC+8)
│   │                              #       Mirrors engine's biz_today() — both must change together
│   ├── obs.py                      # 62L  Logging config: setup_logging() idempotent, stderr + rotating file
│   │                              #       Channel tree dca.*, propagate=False, 1MB×3 rotation
│   ├── state.py                    # 113L session_state key registry — 11 constants + ALL_KEYS + invalidate_sync()
│   │                              #       Single source of truth for key names; tests assert no drift
│   ├── market_cache.py             # 38L  Pure functions for curves.py: cache_file_for, load_cached_closes,
│   │                              #       close_at_or_before — no engine/Streamlit dependency
│   │
│   ├── services/
│   │   ├── __init__.py             # 2L
│   │   ├── model.py                # 61L  run_model() — subprocess call to dca_calculator.py, JSON parse
│   │   │                          #       st.cache_data 900s, cache key = (amount, user)
│   │   ├── quotes.py              # 101L fetch_xau_spot(), fetch_btc(paths) — sidebar quote cards
│   │   │                          #       BTC fallback: btc_last.json + stale_label annotation
│   │   └── curves.py              # 97L  load_price_series(), portfolio_curve() — net value chart
│   │                              #       Uses src/market_cache.py (no sys.path hack, no engine import)
│   │
│   ├── ui/
│   │   ├── __init__.py             # 2L
│   │   ├── styles.py               # 185L Global CSS injection via st.markdown(unsafe_allow_html=True)
│   │   ├── overlays.py             # 59L  Three masks: auth, sync, loading (opacity must be set)
│   │   ├── sidebar.py              # 339L Sidebar: user mgmt, quote cards, amount form, budget, FX, disclaimer
│   │   │                          #       render(paths, user) → Decision (result/dec/ms/pf)
│   │   │                          #       Model execution point: first run + amount re-run
│   │   └── auth.py                 # 365L Auth gate: require_user() — 3-stage state machine
│   │                              #       login → activate → bootstrap; fail-closed; PBKDF2 + lockout
│   │
│   └── tabs/
│       ├── __init__.py             # 6L
│       ├── today.py                # 99L  Tab1: today's suggestion (amount, deploy coef, allocation, 3-tier plan)
│       │                          #       Degradation banner when market data unavailable
│       ├── holdings.py             # 78L  Tab2: portfolio summary, valuation, unrealized PnL, XIRR, curve
│       ├── records.py              # 169L Tab3: record transaction / skip observation → confirm → storage.append_row
│       │                          #       Same-day-same-asset-same-direction dedup, force=True override
│       ├── history.py              # 26L  Tab4: read-back transactions / observations
│       ├── backtest.py             # 249L Tab5: 5-section static backtest report (reads backtest/*.json)
│       └── strategy_doc.py         # 18L  Tab6: renders strategy/core-strategy.md (single source of truth)
│
├── data/                           # ── Engine data directory (engine's only data source) ──
│   ├── config.json                 #      Strategy params: weights, ranges, scoring coefficients
│   ├── transactions.csv            #      Ledger (local-only mode; cloud uses users/<user>/)
│   ├── observations.csv            #      Skip/observation log (same)
│   ├── budget_overrides.json       #      Monthly budget overrides
│   ├── fx_last.json                #      FX last-success fallback (per-field fetched_at)
│   ├── market_live.json            #      Intraday unclosed bars (merge on load, csv takes priority)
│   ├── quote_snapshot.json         #      Market snapshot (TTL 600s, skips re-fetch)
│   ├── xau_spot_last.json          #      XAU spot price fallback
│   ├── btc_last.json               #      BTC price fallback (stale_label: "⚠️更新失败，使用历史数据")
│   ├── *.localbak                  #      Rotating backups (10 copies, timestamped)
│   ├── users/                      #      Per-user local cache (cloud mode, sync_local generates)
│   ├── data/                       #      [gitignored] Accidental nested dir from --base-dir data runs
│   └── market_history/             #      Closed-bar historical closes (6 CSV files, 2 cols: date,close)
│       ├── _GSPC.csv               #      S&P 500 index
│       ├── SPY.csv                 #      S&P 500 ETF
│       ├── _NDX.csv                #      Nasdaq 100 index
│       ├── QQQ.csv                 #      Nasdaq 100 ETF
│       ├── GC_F.csv                #      Gold futures
│       └── XAUT_USD.csv            #      Gold token
│
├── tests/                          # ── Offline regression suite (130 tests, all offline) ──
│   ├── conftest.py                 # 143L Autouse: _deny_network (socket/DNS/subprocess), _quarantine_logging
│   ├── test_engine.py              # 495L Engine pure-function tests (46 tests)
│   ├── test_storage.py             # 297L Storage local + Sheets safe-path tests (25 tests)
│   ├── test_smoke.py               # 454L AppTest full-page smoke (20 tests, patched run_model + quotes)
│   ├── test_state.py               # 223L session_state key registry tests (17 tests)
│   ├── test_obs.py                 # 213L Logging config tests (7 tests)
│   ├── test_offline_guard.py       # 74L  Offline guard self-test (8 tests)
│   ├── test_backup_script.py       # 117L Backup Code.gs source invariants (5 tests)
│   └── test_deprecated_api.py      # 71L  Deprecated API zero-residue + lower-bound (2 tests)
│
├── deploy/                         # ── Deployment & external access ──
│   ├── DEPLOY.md                   # 184L Three real paths: Cloud (prod) / local / ngrok
│   ├── backup/
│   │   └── Code.gs                 # 201L Apps Script daily backup (4-table CSV + manifest → Drive)
│   │                              #       30-day retention, email alert, restoreSnapshot() with guard
│   ├── start-dca-tunnel.bat        # 41L  ngrok tunnel launcher (ASCII only — cmd reads OEM 936)
│   └── bin/
│       └── ngrok.exe               # 33MB NOT in git — must re-download if deleted
│
├── backtest/                       # ── One-time backtest products (not runtime dependency) ──
│   ├── backtest_dca.py             # 235L Archived script — DCA strategy backtest
│   ├── backtest_single.py          # 183L Archived script — single-asset dynamic vs fixed
│   ├── backtest_compare3.py        # 241L Archived script — 3-strategy comparison
│   ├── results_rolling.json        #      Frozen product — Tab5 sections 3+4 read this
│   ├── results_compare3.json       #      Frozen product — Tab5 section 1
│   ├── results_single_compare.json #      Frozen product — Tab5 section 3
│   ├── results.json                #      Frozen intermediate
│   ├── results_single.json         #      Frozen intermediate
│   ├── results.md                  #      Frozen text report
│   └── compare3.md                 #      Frozen text report
│
├── strategy/
│   └── core-strategy.md            # 184L Strategy doc — single source of truth, Tab6 renders directly
│
├── docs/                           # ── Documentation ──
│   ├── README.md                   # 46L  Doc portal — index of all docs (active/frozen, audience, update timing)
│   ├── ARCHITECTURE.md             # 365L Top-level architecture — single source of truth for structure
│   ├── ARCHITECTURE-DETAIL.md      # 412L Architecture detail — implementation, design motives, pitfalls
│   ├── BUGLIST.md                  # 2107L Bug ledger — 35 bugs (32✅ + 3⚪), 4-stage workflow
│   ├── CODE_REVIEW_2026-08-24.md   # 103L Architecture code review report
│   └── plans/                      #      Design docs and historical audits
│       ├── app-split-design.md     # 342L app.py 7-cut split plan (completed)
│       ├── architecture-and-p0-explained.md # 725L Original architecture + P0 audit
│       ├── project-audit-2026-08-17.md # 358L Original audit snapshot (frozen)
│       ├── distributed-pondering-puppy.md  # 238L
│       ├── proud-discovering-kitten.md     # 170L
│       └── toasty-yawning-dewdrop.md       # 226L
│
├── logs/                           # Runtime log — dca.log (1MB×3 rotation, *.log not in git)
│
├── CHANGELOG.md                    # Human-readable commit log — one line per commit with HH:MM:SS
│                                  #       Generated/verified by scripts/changelog.py
├── CLAUDE.md                       # AI assistant project guide
├── README.md                       # Project readme
└── STRUCTURE.md                    # This file
```

---

## 中文注释版

```
sp500-nasdaq100-gold-dca/
│
├── app.py                          # 70行  Streamlit 主程序——纯装配层
│                                  #       import → 构建路径 → 配置日志 → 初始化存储
│                                  #       → 认证门闸 → 侧边栏 → 6个tab渲染调用
│
├── storage.py                      # 654行 存储层——所有数据I/O的唯一入口
│                                  #       Google Sheets（主）/ 本地CSV（降级回退）
│                                  #       PBKDF2认证、写前快照、去重、按用户隔离
│                                  #       19个公开函数；build_tx_row / build_obs_row 行字典构造器
│
├── start-app.bat                   # 5行   本机Windows双击启动
│
├── requirements.txt                # 17行  Cloud/Linux可安装范围——6个直接依赖带版本上下界
├── requirements-dev.lock           # 82行  Windows/Python 3.14精确锁定——完整pip freeze可复现
├── pytest.ini                      # 7行   pytest配置——只收tests/，排除回测脚本
├── .gitignore                      # 50行  忽略 .venv、logs/*.log、data/users/、secrets、btc_last.json、data/data/
├── .gitattributes                  # 6行   Windows CRLF规范化
├── .dockerignore                   # 16行  Docker安全默认（当前未用；Docker文件已删）
│
├── .github/
│   └── workflows/
│       └── ci.yml                  # 47行  GitHub Actions——push main触发 Windows/3.14 + Linux/3.12 pytest
│
├── .streamlit/
│   ├── config.toml                 # 8行   主题配色
│   ├── secrets.toml                #       GCP服务账号凭据（不入库）
│   └── secrets.toml.example        # 18行  凭据模板
│
├── scripts/                        # ── 计算引擎（6个模块，线性依赖DAG，无循环引用）──
│   │
│   ├── dca_calculator.py           # 240行 入口：main() + stdout/stderr编码修正 + re-export
│   │                              #       薄壳——52个公共符号全部从兄弟模块re-export
│   │                              #       `import dca_calculator as eng` 调用方零改动
│   │                              #       命令行：--amount --base-dir --history-years --user --snapshot-ttl
│   │                              #       输出：JSON到stdout（18个顶层键）
│   │
│   ├── dca_types.py                # 211行 数据结构、工具函数、记账数据加载
│   │                              #       DEFAULT_CONFIG, _BIZ_TZ, biz_today(), is_iso_date(), utc_today()
│   │                              #       Transaction, read_json, resolve_monthly_budget, as_float
│   │                              #       read_transactions, read_observations, read_last_observation
│   │                              #       trading_days_in_month, monthly_budget_status
│   │
│   ├── dca_market.py               # 524行 行情抓取、缓存I/O、汇率获取
│   │                              #       fetch_json（Yahoo Chart v8，3次退避重试）
│   │                              #       fetch_chart, metrics_from_closes, pairs_from_chart_result
│   │                              #       sanitize_symbol, cache_file_for, load_cached_closes
│   │                              #       close_at_or_before, save_cached_closes（三道护栏写入）
│   │                              #       load_market_live, save_market_live, split_live_bars, merge_live_bars
│   │                              #       _yfinance_closes（兜底，不落库）
│   │                              #       get_symbol_history, fetch_history（ThreadPoolExecutor并发）
│   │                              #       fetch_usdcny, fetch_usdtusd, load_fx_last, save_fx_last, _fx_entry
│   │                              #       load_quote_snapshot, save_quote_snapshot（TTL 600秒）
│   │                              #       market_symbol_for_asset
│   │
│   ├── dca_portfolio.py            # 131行 组合持仓计算
│   │                              #       xnpv, xirr（牛顿迭代法XIRR）
│   │                              #       portfolio_summary（价格代理查找、汇率选择、浮盈亏、收益率）
│   │
│   ├── dca_scoring.py              # 247行 评分模型与决策引擎
│   │                              #       clip, DEFAULT_MODEL, asset_score
│   │                              #       level_label, neutral_weights, score_based_weights
│   │                              #       market_freshness（主闸：latest_source != "quote"；副闸：>10天）
│   │                              #       build_decision（评分→部署系数×节奏系数→金额
│   │                              #                     →权重倾斜→资产比例）
│   │
│   ├── dca_table.py                # 153行 宽表渲染
│   │                              #       WIDE_TABLE_HEADER, _money, _pct, _num, _xirr_cell
│   │                              #       build_wide_rows（list[dict]结构化中间体）
│   │                              #       render_wide_table（从build_wide_rows生成markdown）
│   │                              #       asset_note
│   │
│   ├── dca_action.py               # 190行 Claude Skill业务动作CLI
│   │                              #       `record tx` / `record obs` / `override` 子命令
│   │                              #       与Web共用storage.py——相同校验、相同schema
│   │                              #       按金额自动换算shares；写后自动sync_local刷新缓存
│   │
│   └── changelog.py                # 115行 CHANGELOG维护工具
│                                  #       `add <hash>`——从git提交时刻生成行
│                                  #       `--check`——校验所有commit都有行且时刻正确
│
├── src/                            # ── Web应用业务层（app.py是纯装配）──
│   │
│   ├── __init__.py                 # 2行   包标记
│   ├── context.py                  # 73行  启动上下文：Paths dataclass、Decision dataclass、build_paths()
│   ├── dates.py                    # 20行  业务"今天"——biz_today()（Asia/Shanghai，固定UTC+8）
│   │                              #       与引擎biz_today()同规则双实现——两处必须同改
│   ├── obs.py                      # 62行  日志配置：setup_logging()幂等，stderr + 轮转文件
│   │                              #       频道树dca.*，propagate=False，1MB×3轮转
│   ├── state.py                    # 113行 session_state键登记表——11个常量 + ALL_KEYS + invalidate_sync()
│   │                              #       键名单一事实源；测试断言不漂移
│   ├── market_cache.py             # 38行  curves.py的纯函数：cache_file_for, load_cached_closes,
│   │                              #       close_at_or_before——不依赖引擎/Streamlit
│   │
│   ├── services/
│   │   ├── __init__.py             # 2行
│   │   ├── model.py                # 61行  run_model()——子进程调用dca_calculator.py，解析JSON
│   │   │                          #       st.cache_data 900秒，缓存键 =（金额，用户）
│   │   ├── quotes.py              # 101行 fetch_xau_spot(), fetch_btc(paths)——侧边栏行情卡片
│   │   │                          #       BTC兜底：btc_last.json + stale_label标注
│   │   └── curves.py              # 97行  load_price_series(), portfolio_curve()——净值曲线
│   │                              #       用src/market_cache.py（无sys.path hack，不import引擎）
│   │
│   ├── ui/
│   │   ├── __init__.py             # 2行
│   │   ├── styles.py               # 185行 全局CSS注入，st.markdown(unsafe_allow_html=True)
│   │   ├── overlays.py             # 59行  三个遮罩：认证、同步、加载（必须设不透明background）
│   │   ├── sidebar.py              # 339行 侧边栏：用户管理、行情卡片、金额表单、预算、汇率、免责声明
│   │   │                          #       render(paths, user) → Decision（result/dec/ms/pf）
│   │   │                          #       模型执行点：首跑 + 金额重跑
│   │   └── auth.py                 # 365行 认证门闸：require_user()——三阶段状态机
│   │                              #       login → activate → bootstrap；fail-closed；PBKDF2 + 锁定
│   │
│   └── tabs/
│       ├── __init__.py             # 6行
│       ├── today.py                # 99行  Tab1：今日建议（金额、部署系数、三资产分配、三档执行方案）
│       │                          #       行情不可用时顶部降级横幅
│       ├── holdings.py             # 78行  Tab2：持仓汇总、估值、浮盈亏、XIRR、净值曲线
│       ├── records.py              # 169行 Tab3：回报成交/主动跳过 → 确认 → storage.append_row
│       │                          #       同日同资产同方向去重，force=True显式放行
│       ├── history.py              # 26行  Tab4：回读 transactions / observations
│       ├── backtest.py             # 249行 Tab5：5段静态回测报告（读backtest/*.json）
│       └── strategy_doc.py         # 18行  Tab6：渲染strategy/core-strategy.md（唯一事实源）
│
├── data/                           # ── 引擎数据目录（引擎唯一数据来源）──
│   ├── config.json                 #      策略参数：权重、区间、评分系数
│   ├── transactions.csv            #      成交记录（仅本地模式；云端用users/<user>/）
│   ├── observations.csv            #      跳过/观察记录（同上）
│   ├── budget_overrides.json       #      月度预算覆盖
│   ├── fx_last.json                #      汇率上次成功值兜底（分字段带fetched_at）
│   ├── market_live.json            #      当日未收盘K线（加载时merge，csv优先）
│   ├── quote_snapshot.json         #      行情快照（TTL 600秒，命中跳过抓价）
│   ├── xau_spot_last.json          #      XAU现货价兜底
│   ├── btc_last.json               #      BTC价格兜底（stale_label: "⚠️更新失败，使用历史数据"）
│   ├── *.localbak                  #      轮转留底（10份，带时间戳）
│   ├── users/                      #      每用户本地缓存（云端模式，sync_local生成）
│   ├── data/                       #      [gitignored] --base-dir data误跑产生的嵌套目录
│   └── market_history/             #      已收盘定稿收盘价（6个CSV，两列：date,close）
│       ├── _GSPC.csv               #      标普500指数
│       ├── SPY.csv                 #      标普500 ETF
│       ├── _NDX.csv                #      纳指100指数
│       ├── QQQ.csv                 #      纳指100 ETF
│       ├── GC_F.csv                #      黄金期货
│       └── XAUT_USD.csv            #      黄金代币
│
├── tests/                          # ── 离线回归测试套件（130条，全离线）──
│   ├── conftest.py                 # 143行 autouse：_deny_network（socket/DNS/子进程），_quarantine_logging
│   ├── test_engine.py              # 495行 引擎纯函数测试（46条）
│   ├── test_storage.py             # 297行 存储层本地+Sheets安全路径测试（25条）
│   ├── test_smoke.py               # 454行 AppTest整页冒烟（20条，patch run_model + quotes）
│   ├── test_state.py               # 223行 session_state键登记表测试（17条）
│   ├── test_obs.py                 # 213行 日志配置测试（7条）
│   ├── test_offline_guard.py       # 74行  拒网守卫自测（8条）
│   ├── test_backup_script.py       # 117行 备份Code.gs源码不变量（5条）
│   └── test_deprecated_api.py      # 71行  弃用API零残留 + 下界托底（2条）
│
├── deploy/                         # ── 部署与外发 ──
│   ├── DEPLOY.md                   # 184行 三条真实路径：Cloud（生产）/ 本机 / ngrok
│   ├── backup/
│   │   └── Code.gs                 # 201行 Apps Script每日备份（四表CSV + manifest → Drive）
│   │                              #       30天保留、邮件告警、restoreSnapshot()带守卫
│   ├── start-dca-tunnel.bat        # 41行  ngrok隧道启动器（只能写ASCII——cmd按OEM 936读）
│   └── bin/
│       └── ngrok.exe               # 33MB 不入库——删了只能重下
│
├── backtest/                       # ── 一次性回测产物（非运行时依赖）──
│   ├── backtest_dca.py             # 235行 归档脚本——DCA策略回测
│   ├── backtest_single.py          # 183行 归档脚本——单品种动态vs固定
│   ├── backtest_compare3.py        # 241行 归档脚本——三策略对比
│   ├── results_rolling.json        #      冻结产物——Tab5第3+4段读它
│   ├── results_compare3.json       #      冻结产物——Tab5第1段
│   ├── results_single_compare.json #      冻结产物——Tab5第3段
│   ├── results.json                #      冻结中间产物
│   ├── results_single.json         #      冻结中间产物
│   ├── results.md                  #      冻结文字报告
│   └── compare3.md                 #      冻结文字报告
│
├── strategy/
│   └── core-strategy.md            # 184行 策略说明——唯一事实源，Tab6直接渲染
│
├── docs/                           # ── 文档 ──
│   ├── README.md                   # 46行  文档门户——全部说明文件索引（活/冻标注、读者、更新时机）
│   ├── ARCHITECTURE.md             # 365行 顶层架构——结构唯一事实源
│   ├── ARCHITECTURE-DETAIL.md      # 412行 架构详设——实现细节、设计动机、踩坑记录
│   ├── BUGLIST.md                  # 2107行 问题台账——35条（32✅ + 3⚪），四段式工作流
│   ├── CODE_REVIEW_2026-08-24.md   # 103行 架构代码审查报告
│   └── plans/                      #      设计文档与历史审计
│       ├── app-split-design.md     # 342行 app.py七刀拆分方案（已完成）
│       ├── architecture-and-p0-explained.md # 725行 原始架构+P0审计
│       ├── project-audit-2026-08-17.md # 358行 原始审计快照（冻结）
│       ├── distributed-pondering-puppy.md  # 238行
│       ├── proud-discovering-kitten.md     # 170行
│       └── toasty-yawning-dewdrop.md       # 226行
│
├── logs/                           # 运行日志——dca.log（1MB×3轮转，*.log不入库）
│
├── CHANGELOG.md                    # 人读版改动流水——每commit一行带HH:MM:SS时刻
│                                  #       由scripts/changelog.py生成/校验
├── CLAUDE.md                       # AI编程助手项目说明
├── README.md                       # 项目自述
└── STRUCTURE.md                    # 本文件
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

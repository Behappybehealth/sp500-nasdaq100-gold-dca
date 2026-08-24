"""整页冒烟：真跑 `app.py` 的装配与全部六个 tab（离线，不碰真实 data/、不碰云端）。

**唯一被换掉的是"抓网络"那一层。** `run_model`（subprocess 起引擎）被替换成用引擎
自己的纯函数**现场组装**的结果——`monthly_budget_status` / `build_decision` /
`market_freshness` / `portfolio_summary` / `render_wide_table` 全部真调，只有 markets
段与汇率段是虚构数值。这样做有三个理由：

1. 真跑子进程必然联网抓行情，直接违反"测试全部离线"（CI 被限流那种红是噪音不是信号）；
2. 真实引擎输出含持仓成本与成交明细，存成 fixture 等于把私人财务数据写进入库文件；
3. 组装照抄 `main()` 的顺序，且顶层键集合由 `test_result_keys_match_engine_output`
   对着引擎源码校验——引擎改了输出形状，这里立刻红，不会让冒烟测试继续验一个过期的形状。

行情/成交的日期全部相对 `biz_today()` 生成，所以这些测试不会"过几天自己坏掉"。
"""

from __future__ import annotations

import ast
import json
import math
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import dca_calculator as eng
import storage
from src.ui import sidebar

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app.py"

# 六个 tab 的标题，与 app.py 的 st.tabs 一致
TAB_TITLES = ["🎯 今日模拟", "📊 持仓与曲线", "✍️ 记账", "📜 历史记录", "🧪 回测结果", "📖 策略说明"]

# ---------- 虚构行情：数量级像真的，数值全是编的 ----------
FAKE_PRICES = {
    "^GSPC": 6500.0, "SPY": 648.0,
    "^NDX": 24000.0, "QQQ": 580.0,
    "GC=F": 3400.0, "XAUT-USD": 3402.5,
}
FAKE_USDCNY = 7.1234
FAKE_USDTUSD = 1.0005
FAKE_TS = 1755600000.0  # 固定时刻戳，避免测试随当前时间漂
SERIES_DAYS = 300


# ============================================================
# 虚构引擎输出（用引擎自己的纯函数组装）
# ============================================================

def _closes(px: float, today: date) -> dict:
    """虚构收盘序列：从 px 的 80% 平滑爬到 px，叠一点周期波动。不是真行情。"""
    out = {}
    for i in range(SERIES_DAYS):
        day = today - timedelta(days=SERIES_DAYS - 1 - i)
        ramp = 0.8 + 0.2 * i / (SERIES_DAYS - 1)
        out[day.isoformat()] = round(px * ramp * (1 + 0.01 * math.sin(i / 7)), 4)
    return out


def _market(sym: str, px: float, today: date, *, live: bool) -> dict:
    """单个标的的行情段：指标用引擎的 metrics_from_closes 真算，只有价格是虚构的。"""
    closes = _closes(px, today)
    ordered = [closes[d] for d in sorted(closes)]
    mk = eng.metrics_from_closes(ordered, px, min(closes), today.isoformat())
    mk["symbol"] = sym
    mk["day_change"] = 0.0042
    if live:
        mk["latest_source"] = "quote"  # 拿到 regularMarketPrice → 新鲜度闸放行
        mk["quote_time"] = FAKE_TS
        mk["data_source"] = "chart"
    else:
        mk["latest_source"] = "cache_stale"  # 只有旧收盘价 → 主闸拦下
        mk["cache_warning"] = "虚构：本次未取到实时价"
        mk["data_source"] = "cache"
    return mk


def _txs(today: date) -> list:
    """两笔虚构成交（金额/价格/数量全部编造，与任何真实账本无关）。"""
    return [
        eng.Transaction(date=(today - timedelta(days=20)).isoformat(), action="buy",
                        asset="sp500", symbol="SPY", currency="U", amount_rmb=1500.0,
                        price=640.0, shares=0.3292, fee_rmb=0.0, fx_rate=7.1, notes="虚构"),
        eng.Transaction(date=(today - timedelta(days=10)).isoformat(), action="buy",
                        asset="gold", symbol="XAUT", currency="U", amount_rmb=900.0,
                        price=3380.0, shares=0.0374, fee_rmb=0.0, fx_rate=7.12, notes="虚构"),
    ]


def build_result(config: dict, today: date, *, txs: list, live: bool = True,
                 fx_live: bool = True) -> dict:
    """按 dca_calculator.main() 的顺序组装一份引擎输出（网络段虚构、计算段真调）。"""
    assets = config["assets"]
    markets = {sym: _market(sym, px, today, live=live) for sym, px in FAKE_PRICES.items()}

    model_cfg = dict(eng.DEFAULT_MODEL)
    model_cfg.update(config.get("model", {}) or {})

    month_prefix = today.strftime("%Y-%m")
    month_start = min((t.date for t in txs if t.date.startswith(month_prefix)), default=None)
    release = config.get("month_end_release", {}) or {}
    month_status = eng.monthly_budget_status(
        txs, float(config.get("monthly_budget_rmb", 30000)), today,
        interval_days=int(max(config.get("preferred_trade_interval_days") or [3])),
        release_window_days=int(release.get("window_days", 7)) if release.get("enabled", True) else 0,
        month_start_date=month_start,
    )
    month_status["budget_source"] = "default"

    signal = {eng.market_symbol_for_asset(k, i): markets.get(eng.market_symbol_for_asset(k, i), {})
              for k, i in assets.items()}
    decision = eng.build_decision(signal, assets, model_cfg, month_status, None)
    freshness = eng.market_freshness(signal, today)
    if freshness["degraded"]:
        decision["suggested_amount_rmb"] = 0.0
        decision["level_label"] = "行情不可用·暂停出金额"
        decision["reason"] = (
            f"⚠️ 行情不可用于决策（{freshness['reason']}）"
            f" → 本次不出金额，只展示持仓。原评分供参考：{decision['reason']}"
        )
    decision["degraded"] = freshness["degraded"]
    decision["freshness"] = freshness

    usdcny = FAKE_USDCNY
    usdtcny = round(FAKE_USDTUSD * FAKE_USDCNY, 6)
    fx = {"usdcny": {"value": usdcny, "live": fx_live, "as_of": FAKE_TS},
          "usdtusd": {"value": FAKE_USDTUSD, "live": fx_live, "as_of": FAKE_TS}}

    last_day = max((t.date for t in txs), default=None)
    since_last_record = {}
    if last_day:
        for key, info in assets.items():
            sym = eng.market_symbol_for_asset(key, info)
            then = eng.close_at_or_before(_closes(FAKE_PRICES[sym], today), last_day)
            latest = markets[sym]["latest_price"]
            if then and latest:
                since_last_record[info["name_cn"]] = {
                    "symbol": sym, "last_record_date": last_day,
                    "price_then": then, "latest_price": latest,
                    "change_pct": latest / then - 1,
                }

    result = {
        "as_of": today.isoformat(),
        "input_amount_rmb": None,
        "effective_amount_rmb": round(decision["suggested_amount_rmb"], 2),
        "usdcny": usdcny,
        "usdtcny": usdtcny,
        "fx": fx,
        "monthly_budget_status": month_status,
        "config": config,
        "has_local_transactions": bool(txs),
        "invalid_transactions": [],
        "last_records": [t.__dict__ for t in txs if t.date == last_day],
        "since_last_record": since_last_record,
        "markets": markets,
        "quote_snapshot": {"used": False, "age_s": None, "ttl_s": 600},
        "portfolio": eng.portfolio_summary(txs, markets, assets, usdcny, usdtcny),
        "decision": decision,
        "suggested_weights": decision["weights"],
    }
    result["wide_table_rows"] = eng.build_wide_rows(result, txs, assets)
    result["wide_table_markdown"] = eng.render_wide_table(result, txs, assets)
    return result


def _engine_result_keys() -> set:
    """从引擎源码里抠出 main() 实际输出的顶层键（AST 解析，不需要跑引擎、不联网）。

    只走 `main()` 的函数体：引擎里 `fetch_chart` 那几处也把返回值叫 `result`
    （:504 / :530 / :637 / :649），全模块 walk 会把它们的赋值一并算进契约，
    将来那边一旦出现 `result["x"] = ...` 就会污染键集、报一个查不到源头的失败。
    """
    tree = ast.parse((REPO / "scripts" / "dca_calculator.py").read_text(encoding="utf-8"))
    main_fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"), None
    )
    assert main_fn is not None, "引擎里找不到 main()——契约断言失去依据"

    keys: set = set()
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Call):
            # result.update(...) 会绕过下面两种字面量形态，键集就不再是全集。
            fn = node.func
            assert not (
                isinstance(fn, ast.Attribute) and fn.attr == "update"
                and isinstance(fn.value, ast.Name) and fn.value.id == "result"
            ), "main() 用了 result.update()，AST 取键法已失效，需改断言方式"
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Dict) and any(
            isinstance(t, ast.Name) and t.id == "result" for t in node.targets
        ):
            keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        for t in node.targets:
            if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                    and t.value.id == "result" and isinstance(t.slice, ast.Constant)):
                keys.add(t.slice.value)
    return keys


# ============================================================
# 隔离夹具：本机模式 + tmp 数据目录 + 零网络
# ============================================================

@pytest.fixture
def run_app(monkeypatch, tmp_path):
    """返回一个 `run(**kw)` 工厂：跑一趟 app.py，返回 (AppTest, 虚构 result)。

    隔离手段逐条对应一个真实风险：
    - `DCA_AUTH_MODE=local`  → 绕开登录门闸（否则 require_user 会 st.stop）
    - `--base-dir tmp`       → 读写全落 tmp，真实 data/ 一个字节都不动
    - `sheets_enabled=False` → 仓库里有真实 secrets.toml，不显式关掉会写进云端表格
    - patch `run_model`      → 不起子进程、不联网
    - patch 行情/BTC 抓取     → 侧栏那两个 curl 调用也得断网
    """
    base = tmp_path / "base"
    data_dir = base / "data"
    data_dir.mkdir(parents=True)
    shutil.copy(REPO / "data" / "config.json", data_dir / "config.json")
    config = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))

    monkeypatch.setenv("DCA_AUTH_MODE", "local")
    monkeypatch.setattr("sys.argv", ["app.py", "--base-dir", str(base)])
    monkeypatch.setattr(storage, "sheets_enabled", lambda: False)
    monkeypatch.setattr(storage, "_LOCAL_BASE", data_dir)
    storage.init(data_dir)

    def run(*, txs=(), live=True, fx_live=True, quotes=True, write_ledger=True):
        txs = list(txs)
        if txs and write_ledger:
            for t in txs:  # 走真实写入路径建账本，schema 不可能对不上
                storage.append_row("transactions", "local", dict(t.__dict__))
            cache_dir = data_dir / "market_history"
            cache_dir.mkdir(exist_ok=True)
            today = eng.biz_today()
            for sym in ("SPY", "QQQ", "XAUT-USD"):
                rows = sorted(_closes(FAKE_PRICES[sym], today).items())
                eng.cache_file_for(cache_dir, sym).write_text(
                    "date,close\n" + "".join(f"{d},{c}\n" for d, c in rows), encoding="utf-8"
                )
        result = build_result(config, eng.biz_today(), txs=txs, live=live, fx_live=fx_live)
        monkeypatch.setattr(sidebar, "run_model", lambda amount, user, paths: result)
        monkeypatch.setattr(sidebar, "fetch_xau_spot", lambda paths: (
            {"price": 3401.2, "chg_pct": 0.003, "ts": FAKE_TS, "stale": False} if quotes else None))
        monkeypatch.setattr(sidebar, "fetch_btc", lambda paths: (
            {"price": 96000.0, "chg_pct": -0.012, "ts": FAKE_TS} if quotes else None))
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        return at, result

    yield run
    storage._SHEET_CACHE.clear()


def _texts(elements) -> str:
    return "\n".join(str(e.value) for e in elements)


def _metric(at, label: str):
    hits = [m for m in at.metric if m.label == label]
    assert hits, f"页面上找不到指标「{label}」，实际有：{[m.label for m in at.metric]}"
    return hits[0]


# ============================================================
# 1. 整页能跑起来
# ============================================================

def test_app_runs_without_exception(run_app):
    """最基本的一条：整页装配 + 六个 tab 渲染完不抛异常。"""
    at, _ = run_app()
    assert not at.exception, at.exception


def test_all_six_tabs_render(run_app):
    """六个 tab 齐、顺序不变——tab 少一个或串位都是可见的功能缺失。"""
    at, _ = run_app()
    assert [t.label for t in at.tabs] == TAB_TITLES


def test_local_mode_banner_is_visible(run_app):
    """单机模式必须显式告知（数据只在本机 CSV），不能静默降级。"""
    at, _ = run_app()
    assert "单机模式" in _texts(at.warning)


def test_result_keys_match_engine_output(run_app):
    """虚构 result 的顶层键集合必须与引擎源码里 main() 的输出一致。

    这是本文件的防腐条：引擎加/删顶层键时先红在这里，提醒同步 fixture，
    而不是让冒烟测试继续对着一个过期形状"绿得毫无意义"。
    """
    _, result = run_app()
    assert set(result) == _engine_result_keys()


# ============================================================
# 2. Tab1 今日模拟：四个指标与降级闸
# ============================================================

def test_today_tab_shows_four_metrics(run_app):
    """今日建议/部署系数/本月可用池/每日基准四个指标齐，且建议金额与决策一致。"""
    at, result = run_app()
    dec, ms = result["decision"], result["monthly_budget_status"]
    assert _metric(at, "今日建议").value == f"¥{dec['suggested_amount_rmb']:,.0f}"
    assert _metric(at, "今日建议").delta == dec["level_label"]
    assert _metric(at, "部署系数").value == f"{dec['deploy_multiplier']:.2f}"
    assert _metric(at, "本月可用池").value == f"¥{ms['available_pool_rmb']:,.0f}"
    assert _metric(at, "每日基准").value == f"¥{ms['daily_reference_rmb']:,.0f}"


def test_degraded_market_blocks_amount(run_app):
    """拿不到实时价 → 页面必须报"行情不可用于决策"且金额为 0。

    这是最危险的静默失败（旧收盘价冒充实时价）的可见性回归：降级不可见等于没降级。
    """
    at, result = run_app(live=False)
    assert result["decision"]["degraded"] is True
    assert "行情不可用于决策" in _texts(at.error)
    assert _metric(at, "今日建议").value == "¥0"


def test_live_market_has_no_degrade_banner(run_app):
    """反面对照：实时价正常时不许出现降级横幅（否则告警会被当噪音忽略）。"""
    at, result = run_app()
    assert result["decision"]["degraded"] is False
    assert "行情不可用于决策" not in _texts(at.error)


def test_wide_table_is_rendered(run_app):
    """宽表是引擎给的唯一"全量结果"视图，必须被解析成表格渲染出来。"""
    at, _ = run_app(txs=_txs(eng.biz_today()))
    assert at.dataframe, "整页一张 dataframe 都没有"
    assert "累计持仓结果完整表格" in _texts(at.subheader)


# ============================================================
# 3. 零成交 vs 有持仓两种形态
# ============================================================

def test_new_user_sees_empty_states(run_app):
    """零成交新用户：复盘/曲线/历史三处都给空状态提示，不该报错也不该出现持仓指标。"""
    at, _ = run_app()
    assert not at.exception, at.exception
    info = _texts(at.info)
    assert "本次为第一期测算" in info
    assert "暂无成交记录" in info
    assert not [m for m in at.metric if m.label == "累计投入"]


def test_holdings_user_sees_portfolio_metrics(run_app):
    """有持仓：累计投入/当前市值/未实现盈亏/年化 XIRR 四个指标出现，且累计投入等于账本合计。"""
    txs = _txs(eng.biz_today())
    at, result = run_app(txs=txs)
    assert not at.exception, at.exception
    total = sum(t.amount_rmb for t in txs)
    assert _metric(at, "累计投入").value == f"¥{total:,.0f}"
    assert result["portfolio"]["total_invested_rmb"] == pytest.approx(total)
    for label in ("当前市值", "未实现盈亏", "年化 XIRR"):
        _metric(at, label)


def test_history_tab_lists_written_rows(run_app):
    """历史记录 tab 回读的是刚经 storage 真实写入的账本——写链与读链对得上。"""
    txs = _txs(eng.biz_today())
    at, _ = run_app(txs=txs)
    assert "暂无成交记录" not in _texts(at.info)
    assert storage.read_rows("transactions", "local")[0]["symbol"] == "SPY"


def test_since_last_record_metrics_appear(run_app):
    """有上一条记录时，三资产的"自上次记录以来涨跌"指标要出现（复盘后验数据）。"""
    txs = _txs(eng.biz_today())
    at, result = run_app(txs=txs)
    last_day = max(t.date for t in txs)
    assert set(result["since_last_record"]) == {"标普500", "纳指100", "黄金"}
    labels = [m.label for m in at.metric]
    assert f"标普500（自 {last_day}）" in labels


# ============================================================
# 4. 侧栏：行情三态与汇率三态
# ============================================================

def test_sidebar_shows_all_quote_rows(run_app):
    """侧栏七行实时行情逐行渲染，全部正常时标"（全部正常）"。"""
    at, _ = run_app()
    md = _texts(at.markdown)
    for name, _sym in sidebar.QUOTE_ROWS:
        assert name in md, f"侧栏缺行情行「{name}」"
    assert "（全部正常）" in md


def test_sidebar_marks_stale_quotes(run_app):
    """非实时价必须带 ⚠️缓存 标记，且标题改成"部分异常"。"""
    at, _ = run_app(live=False)
    md = _texts(at.markdown)
    assert "⚠️缓存" in md
    assert "（⚠️ 部分异常）" in md


def test_sidebar_reports_quote_fetch_failure(run_app):
    """XAU/BTC 抓取失败时逐行显示"获取失败"，不许拿别的价蒙过去。"""
    at, _ = run_app(quotes=False)
    md = _texts(at.markdown)
    assert "获取失败，需复核实时价格" in md
    assert "比特币 BTC/USD" in md


def test_sidebar_fx_live_has_no_cache_marker(run_app):
    """汇率实时 → 直接给数，不带缓存标记。"""
    at, result = run_app()
    cap = _texts(at.caption)
    assert f"USD/CNY {result['usdcny']:.4f}" in cap
    assert "U/CNY" in cap and "⚠️缓存" not in cap


def test_sidebar_fx_fallback_shows_cache_marker(run_app):
    """汇率走上次成功值兜底 → 必须带 ⚠️缓存(时刻)，否则用户会把旧汇率当实时用。"""
    at, _ = run_app(fx_live=False)
    cap = _texts(at.caption)
    assert "⚠️缓存" in cap
    assert "USD/CNY" in cap and "U/CNY" in cap


def test_sidebar_budget_falls_back_to_config(run_app):
    """没有月度覆盖时，生效预算取 config 的 monthly_budget_rmb 并标"默认值"。"""
    at, result = run_app()
    budget = float(result["config"]["monthly_budget_rmb"])
    assert f"生效：¥{budget:,.0f}（默认值）" in _texts(at.caption)


# ============================================================
# 5. Tab3 记账表单：默认值来自决策与汇率
# ============================================================

def test_record_form_defaults_come_from_result(run_app):
    """记账表单的日期默认业务今天、汇率默认取实时值——默认值错了就会记错账。"""
    at, result = run_app()
    dates = [w.value for w in at.text_input]
    assert eng.biz_today().isoformat() in dates
    fx_defaults = [w.value for w in at.number_input]
    assert pytest.approx(result["usdcny"], abs=1e-6) in fx_defaults


def test_skip_reason_defaults_to_decision_label(run_app):
    """「今天不买」的跳过原因默认就是决策档位，省得手抄。"""
    at, result = run_app()
    assert result["decision"]["level_label"] in [w.value for w in at.text_input]

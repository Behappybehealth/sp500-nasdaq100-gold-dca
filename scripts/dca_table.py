#!/usr/bin/env python3
"""宽表结构化行与 markdown 渲染。"""
from __future__ import annotations

from dca_market import market_symbol_for_asset
from dca_types import Transaction, as_float


def asset_note(metrics: dict) -> str:
    if not metrics or "latest_price" not in metrics:
        return "行情缺失，需复核实时价格"
    parts = []
    drawdown = metrics.get("drawdown_from_252d_high")
    if drawdown is not None:
        parts.append(f"距252日高点 {drawdown * 100:.1f}%")
    dev = metrics.get("ma_252_deviation")
    if dev is not None:
        side = "高于" if dev >= 0 else "低于"
        parts.append(f"{side}252日均线 {abs(dev) * 100:.1f}%")
    if metrics.get("cache_warning"):
        parts.append("缓存未更新")
    return "；".join(parts) or "—"


WIDE_TABLE_HEADER = [
    "层级", "日期", "期数", "资产", "上一条记录类型", "上一条投入金额RMB",
    "本月已投RMB", "自然月剩余预算RMB", "累计投入RMB", "持仓份额", "最新价格",
    "当前估值RMB", "未实现盈亏RMB", "收益率", "年化XIRR", "组合权重",
    "今日建议金额RMB", "今日建议比例", "备注",
]


def _money(v: float | None) -> str:
    return "暂无" if v is None else f"{v:,.2f}"


def _pct(v: float | None) -> str:
    return "暂无" if v is None else f"{v * 100:.2f}%"


def _num(v: float | None) -> str:
    return "暂无" if v is None else f"{v:,.4f}"


def _xirr_cell(value: float | None, days: int | None) -> str:
    if value is None:
        return "暂无"
    if days is not None and days < 30:
        return "期短不年化"
    return f"{value * 100:.2f}%"


def build_wide_rows(result: dict, transactions: list[Transaction], assets: dict[str, dict]) -> list[dict]:
    """宽表结构化行（list[dict]，列名做 key，值为已格式化字符串）。

    供 render_wide_table（Skill markdown）与 Web DataFrame 共用——
    不再走"结构化→markdown→解析回 DataFrame"的往返。
    """
    today = result.get("as_of", "")
    month = result.get("monthly_budget_status", {})
    portfolio = result.get("portfolio", {})
    markets = result.get("markets", {})
    action = result.get("decision", {})
    scores_map = action.get("scores", {})
    weights = result.get("suggested_weights", {})
    total_suggested = action.get("suggested_amount_rmb") or 0.0
    invested_month = month.get("invested_this_month_rmb", 0.0)
    remaining_budget = month.get("available_pool_rmb", month.get("remaining_budget_rmb", 0.0))
    release_note = "；月末释放中" if month.get("month_end_release_active") else ""

    last_by_asset: dict[str, dict] = {}
    last_total: float | None = None
    last_type = "暂无"
    for rec in result.get("last_records", []):
        if "decision_level" in rec or "total_suggested_rmb" in rec:
            last_type = f"观察/{rec.get('decision_level') or rec.get('action') or ''}"
            continue
        asset_key = rec.get("asset")
        if asset_key:
            last_by_asset[asset_key] = rec
            last_total = (last_total or 0.0) + as_float(str(rec.get("amount_rmb", "0")))
            last_type = rec.get("action") or "buy"

    periods = len({tx.date for tx in transactions}) if transactions else 0
    asset_dates: dict[str, set] = {}
    for tx in transactions:
        asset_dates.setdefault(tx.asset, set()).add(tx.date)

    positions = {p.get("asset"): p for p in portfolio.get("positions", [])}
    rows: list[dict] = []
    rows.append({
        "层级": "组合汇总", "日期": today, "期数": str(periods), "资产": "组合",
        "上一条记录类型": last_type, "上一条投入金额RMB": _money(last_total),
        "本月已投RMB": _money(invested_month), "自然月剩余预算RMB": _money(remaining_budget),
        "累计投入RMB": _money(portfolio.get("total_invested_rmb") or 0.0),
        "持仓份额": "—", "最新价格": "—",
        "当前估值RMB": _money(portfolio.get("current_value_rmb") or 0.0),
        "未实现盈亏RMB": _money(portfolio.get("unrealized_pnl_rmb") or 0.0),
        "收益率": _pct(portfolio.get("return_rate")),
        "年化XIRR": _xirr_cell(portfolio.get("xirr"), portfolio.get("xirr_period_days")),
        "组合权重": "100.00%",
        "今日建议金额RMB": _money(total_suggested), "今日建议比例": "100.00%",
        "备注": f"{action.get('level_label', action.get('level', ''))}：{action.get('reason', '')}{release_note}",
    })
    for key, info in assets.items():
        pos = positions.get(key)
        symbol = market_symbol_for_asset(key, info)
        m = markets.get(symbol) or {}
        w = weights.get(key, 0.0)
        rec = last_by_asset.get(key)
        sc = (scores_map.get(key) or {}).get("score")
        note = asset_note(m) + (f"；评分 {sc:+.2f}" if sc is not None else "")
        rows.append({
            "层级": "资产", "日期": today, "期数": str(len(asset_dates.get(key, set()))),
            "资产": info.get("name_cn", key),
            "上一条记录类型": (rec.get("action") if rec else "暂无"),
            "上一条投入金额RMB": _money(as_float(str(rec.get("amount_rmb", "0"))) if rec else None),
            "本月已投RMB": _money(invested_month), "自然月剩余预算RMB": _money(remaining_budget),
            "累计投入RMB": _money(pos.get("invested_rmb") if pos else 0.0),
            "持仓份额": _num(pos.get("shares") if pos else 0.0),
            "最新价格": _money(m.get("latest_price")),
            "当前估值RMB": _money(pos.get("current_value_rmb") if pos else 0.0),
            "未实现盈亏RMB": _money(pos.get("unrealized_pnl_rmb") if pos else 0.0),
            "收益率": _pct(pos.get("return_rate") if pos else None),
            "年化XIRR": _xirr_cell(pos.get("xirr") if pos else None, pos.get("xirr_period_days") if pos else None),
            "组合权重": _pct(pos.get("portfolio_weight") if pos else None),
            "今日建议金额RMB": _money(round(total_suggested * w, 2)),
            "今日建议比例": f"{w * 100:.1f}%",
            "备注": note,
        })
    rows.append({
        "层级": "现金/待投", "日期": today, "期数": "—", "资产": "现金/待投预算",
        "上一条记录类型": "暂无", "上一条投入金额RMB": "暂无",
        "本月已投RMB": _money(invested_month), "自然月剩余预算RMB": _money(remaining_budget),
        "累计投入RMB": "0.00", "持仓份额": "—", "最新价格": "—",
        "当前估值RMB": _money(remaining_budget), "未实现盈亏RMB": "—",
        "收益率": "—", "年化XIRR": "—", "组合权重": "—",
        "今日建议金额RMB": _money(max(0.0, remaining_budget - total_suggested)),
        "今日建议比例": "—", "备注": "自然月剩余预算",
    })
    return rows


def render_wide_table(result: dict, transactions: list[Transaction], assets: dict[str, dict]) -> str:
    """宽表 markdown（供 Claude Skill 文本消费；Web 端直接用 build_wide_rows 的结构化行）。"""
    rows = build_wide_rows(result, transactions, assets)
    header = WIDE_TABLE_HEADER
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")) for h in header) + " |")
    return "\n".join(lines)

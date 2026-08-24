#!/usr/bin/env python3
"""组合持仓计算：XIRR 与组合汇总。"""
from __future__ import annotations

import math
from datetime import date
from typing import Dict, List, Optional

from dca_types import Transaction, biz_today
from dca_market import market_symbol_for_asset


def xnpv(rate: float, cashflows: list) -> float:
    t0 = cashflows[0][0]
    return sum(amount / (1.0 + rate) ** ((day - t0).days / 365.0) for day, amount in cashflows)


def xirr(cashflows: list, tol: float = 1e-6, max_iter: int = 50) -> Optional[float]:
    """Annualized IRR for irregular cash flows: (date, amount), outflows negative.

    Buys are negative, sells positive, and the current portfolio value is the
    terminal positive flow at today. Returns None when no solution exists.
    """
    flows = sorted(((d, a) for d, a in cashflows if a), key=lambda x: x[0])
    if len(flows) < 2:
        return None
    if not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    t0 = flows[0][0]
    years = [(d - t0).days / 365.0 for d, _ in flows]
    scale = max(1.0, sum(abs(a) for _, a in flows))
    rate = 0.1
    for _ in range(max_iter):
        f = sum(a * (1.0 + rate) ** -y for (_, a), y in zip(flows, years))
        if abs(f) < tol * scale:
            return rate
        df = sum(-y * a * (1.0 + rate) ** (-y - 1.0) for (_, a), y in zip(flows, years))
        if abs(df) < 1e-12:
            break
        new_rate = rate - f / df
        if not math.isfinite(new_rate) or new_rate <= -0.9999:
            new_rate = rate / 2.0 if rate > -0.5 else (rate - 0.9999) / 2.0
        rate = new_rate
    lo, hi = -0.9999, 10.0
    f_lo, f_hi = xnpv(lo, flows), xnpv(hi, flows)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = xnpv(mid, flows)
        if abs(f_mid) < tol * scale:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def portfolio_summary(transactions: List[Transaction], prices: Dict[str, dict], assets: Dict[str, dict], current_fx_rate: Optional[float], usdt_fx_rate: Optional[float] = None) -> dict:
    by_asset: Dict[str, dict] = {}
    flows_by_asset: Dict[str, list] = {}
    all_flows: list = []
    for tx in transactions:
        item = by_asset.setdefault(tx.asset, {"asset": tx.asset, "symbol": tx.symbol, "invested_rmb": 0.0, "shares": 0.0, "fees_rmb": 0.0, "tx_fx_rate": tx.fx_rate})
        sign = -1 if tx.action == "sell" else 1
        item["invested_rmb"] += sign * tx.amount_rmb
        item["shares"] += sign * tx.shares
        item["fees_rmb"] += tx.fee_rmb
        if tx.fx_rate:
            item["tx_fx_rate"] = tx.fx_rate
        try:
            tx_day = date.fromisoformat(tx.date)
        except ValueError:
            tx_day = None
        if tx_day and tx.amount_rmb:
            flow_sign = 1.0 if tx.action == "sell" else -1.0
            flows_by_asset.setdefault(tx.asset, []).append((tx_day, flow_sign * tx.amount_rmb))
            all_flows.append((tx_day, flow_sign * tx.amount_rmb))

    total_invested = sum(v["invested_rmb"] for v in by_asset.values())
    total_value = 0.0
    for item in by_asset.values():
        cfg = assets.get(item["asset"], {})
        # 价格查找：记录代码精确匹配 → 该资产显式声明的 price_proxy_symbols（如 XAUT→XAUT-USD，
        # 同为 1:1 盎司口径，无数量级风险）→ 置空由用户提供现价
        price_info = prices.get(item["symbol"]) or {}
        price = price_info.get("latest_price")
        price_source = item["symbol"] if price else None
        if not price:
            for proxy in cfg.get("price_proxy_symbols", []):
                proxy_price = (prices.get(proxy) or {}).get("latest_price")
                if proxy_price:
                    price = proxy_price
                    price_source = proxy
                    break
        # 汇率不可用（None）时估值置空而不是编数：历史记账汇率不代表当前，置空让 UI 显式告警
        if cfg.get("fx_mode") == "usdt":
            fx_rate = usdt_fx_rate
        elif price:
            fx_rate = current_fx_rate
        else:
            fx_rate = item.get("tx_fx_rate", 1.0)
        item["latest_price"] = price
        item["price_source"] = price_source
        item["market_symbol"] = market_symbol_for_asset(item["asset"], cfg) if cfg else item["symbol"]
        item["current_fx_rate"] = fx_rate
        item["current_value_rmb"] = item["shares"] * price * fx_rate if (price and fx_rate) else None
        if item["current_value_rmb"] is not None:
            total_value += item["current_value_rmb"]
        item["unrealized_pnl_rmb"] = (item["current_value_rmb"] - item["invested_rmb"]) if item["current_value_rmb"] is not None else None
        item["return_rate"] = (item["unrealized_pnl_rmb"] / item["invested_rmb"]) if (item["unrealized_pnl_rmb"] is not None and item["invested_rmb"]) else None
        if item["current_value_rmb"] is not None:
            flows = flows_by_asset.get(item["asset"], [])
            item["xirr"] = xirr(flows + [(biz_today(), item["current_value_rmb"])])
            item["xirr_period_days"] = (biz_today() - min(d for d, _ in flows)).days if flows else None
        else:
            item["xirr"] = None
            item["xirr_period_days"] = None
    for item in by_asset.values():
        item["portfolio_weight"] = item["current_value_rmb"] / total_value if total_value and item["current_value_rmb"] is not None else None

    return {
        "total_invested_rmb": total_invested,
        "current_value_rmb": total_value if total_value else None,
        "unrealized_pnl_rmb": (total_value - total_invested) if total_value else None,
        "return_rate": ((total_value - total_invested) / total_invested) if total_value and total_invested else None,
        "xirr": xirr(all_flows + [(biz_today(), total_value)]) if total_value else None,
        "xirr_period_days": (biz_today() - min(d for d, _ in all_flows)).days if all_flows else None,
        "positions": list(by_asset.values()),
    }

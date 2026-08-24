#!/usr/bin/env python3
"""评分模型与决策引擎。"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from dca_types import is_iso_date
from dca_market import market_symbol_for_asset


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


DEFAULT_MODEL = {
    "base_amount_rmb": 5000,
    "deploy_gain": 1.1,
    "deploy_max": 1.8,
    "skip_below": 0.15,
    "score_weights": {"value": 0.50, "trend": 0.25, "momentum": 0.15, "heat": 0.45, "heat_quad": 0.20, "volatility": 0.15},
    "tilt_strength": 0.9,
    "defense_boost": 0.6,
}


def asset_score(m: dict, w: dict) -> dict:
    """连续吸引力评分：每个分量由当日实时指标归一化，每天都不同。

    关键校准：趋势/动量只防御不追高（封顶），回撤价值受趋势健康门控
    （趋势破坏时回撤是飞刀不是机会），过热带二次项确保极端行情能压到不买。
    """
    if not m or "latest_price" not in m:
        return {"score": None}
    dd = m.get("drawdown_from_252d_high")
    pos = m.get("position_in_252d_range")
    r20 = m.get("return_20d")
    r60 = m.get("return_60d")
    r120 = m.get("return_120d")
    ma60 = m.get("ma_60_deviation")
    ma252 = m.get("ma_252_deviation")
    rsi = m.get("rsi_14")
    vol = m.get("vol_20d_annualized")

    trend_raw = clip((((ma60 or 0.0) + (ma252 or 0.0)) / 2.0) / 0.12, -1.0, 1.0)
    momentum_raw = clip((((r60 or 0.0) + (r120 or 0.0)) / 2.0) / 0.20, -1.0, 1.0)
    value_raw = clip((-(dd or 0.0) - 0.04) / 0.12, -1.0, 1.0)
    trend_health = clip((trend_raw + 1.2) / 1.4, 0.0, 1.0)
    value = value_raw * trend_health
    trend = clip(trend_raw, -1.0, 0.4)
    momentum = clip(momentum_raw, -1.0, 0.5)
    heat = (
        clip(((rsi if rsi is not None else 50.0) - 55.0) / 25.0, 0.0, 1.0) * 0.5
        + clip(((pos if pos is not None else 0.5) - 0.85) / 0.15, 0.0, 1.0) * 0.3
        + clip((r20 or 0.0) / 0.06, 0.0, 1.0) * 0.2
    )
    vol_pen = clip(((vol if vol is not None else 0.15) - 0.18) / 0.15, 0.0, 1.0)

    score = (
        w.get("value", 0.50) * value
        + w.get("trend", 0.25) * trend
        + w.get("momentum", 0.15) * momentum
        - w.get("heat", 0.45) * heat
        - w.get("heat_quad", 0.20) * heat * heat
        - w.get("volatility", 0.15) * vol_pen
    )
    return {
        "score": round(score, 4),
        "value": round(value, 4),
        "trend": round(trend, 4),
        "momentum": round(momentum, 4),
        "heat": round(heat, 4),
        "vol_penalty": round(vol_pen, 4),
    }


def level_label(deploy_mult: float) -> str:
    if deploy_mult >= 1.35:
        return "积极买入"
    if deploy_mult >= 1.05:
        return "建议买入"
    if deploy_mult >= 0.75:
        return "正常推进"
    if deploy_mult >= 0.40:
        return "谨慎少量"
    if deploy_mult >= 0.15:
        return "小额试探"
    return "今日不买，延后观察"


def neutral_weights(assets: Dict[str, dict]) -> Dict[str, float]:
    total = sum(float(i.get("neutral_weight", 0.0)) for i in assets.values()) or 1.0
    return {k: float(i.get("neutral_weight", 0.0)) / total for k, i in assets.items()}


def score_based_weights(scores: Dict[str, dict], assets: Dict[str, dict], model: dict) -> Dict[str, float]:
    """比例由评分连续倾斜：w ∝ 中性权重 × (1 + tilt×评分)，黄金在权益趋势走弱时获防御加成。"""
    tilt = float(model.get("tilt_strength", 0.9))
    defense = float(model.get("defense_boost", 0.6))
    equity_trends = [scores[k]["trend"] for k in assets if k != "gold" and (scores.get(k) or {}).get("trend") is not None]
    eq_trend_avg = sum(equity_trends) / len(equity_trends) if equity_trends else 0.0
    weights: Dict[str, float] = {}
    for key, info in assets.items():
        s = scores.get(key) or {}
        sc = s.get("score")
        sc = 0.0 if sc is None else sc
        if key == "gold":
            sc += defense * max(0.0, -eq_trend_avg)
        neutral = float(info.get("neutral_weight", 0.0))
        weights[key] = max(neutral * 0.2, neutral * (1.0 + tilt * sc))
    lo = {k: float(assets[k].get("min_weight", 0.0)) for k in weights}
    hi = {k: float(assets[k].get("max_weight", 1.0)) for k in weights}
    # 和为 1 与「全部落在 [min, max]」必须同时成立：解 λ 使 Σ clamp(λ·w, lo, hi) = 1。
    # 该和对 λ 单调不减且连续，λ→0 时取 Σlo、λ→∞ 时取 Σhi，故 Σlo ≤ 1 ≤ Σhi 时解必存在。
    if sum(lo.values()) > 1.0 or sum(hi.values()) < 1.0:
        total = sum(weights.values()) or 1.0  # 区间本身容不下 1，只能保住和为 1
        return {k: v / total for k, v in weights.items()}

    def total_at(lam: float) -> float:
        return sum(min(max(lam * weights[k], lo[k]), hi[k]) for k in weights)

    low, high = 0.0, 1.0
    while total_at(high) < 1.0:
        high *= 2.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if total_at(mid) < 1.0:
            low = mid
        else:
            high = mid
    lam = (low + high) / 2.0
    return {k: min(max(lam * v, lo[k]), hi[k]) for k, v in weights.items()}


_MAX_STALE_DAYS = 10


def market_freshness(signal_markets: Dict[str, dict], today: date, max_stale_days: int = _MAX_STALE_DAYS) -> dict:
    """信号标的的新鲜度闸：**判决策实际用的那个价新不新，不判 K 线日期**。

    K 线日期只是价格新鲜度的代理，而代理会在三种与"价新不新"无关的情况下失真：
    休市、数据源 close 为 null 的空洞、24 小时标的的 bar 边界。实测 GC=F 库内
    最后一根 08-18、实时价 08-20 03:35——按 K 线日期判会放行一个落后两天的价格。

    主闸：本次拿到了 regularMarketPrice（`latest_source == "quote"`）才放行。休市
    时它等于上一收盘价，那就是**最新可得的值**，合法；拿不到就是拿不到，不许旧
    收盘价冒充实时价（yfinance 兜底与 cache_stale 都没有 meta，归入拿不到）。

    副闸：K 线日期落后超 max_stale_days 天照样拦——兜住"实时价正常、序列却因
    数据源长期返回 null 而不再前进"。这道是宽松兜底，不是主判据。
    """
    per_symbol: Dict[str, dict] = {}
    reasons: List[str] = []
    for sym, mk in signal_markets.items():
        end = (mk or {}).get("history_end")
        if not mk or mk.get("error") or not end or not is_iso_date(str(end)):
            per_symbol[sym] = {"stale_days": None, "latest_source": None, "quote_time": None}
            reasons.append(f"{sym} 无可用行情")
            continue
        days = (today - date.fromisoformat(str(end))).days
        source = mk.get("latest_source")
        per_symbol[sym] = {
            "stale_days": days,
            "latest_source": source,
            "quote_time": mk.get("quote_time"),
        }
        if source != "quote":
            reasons.append(f"{sym} 拿不到实时价（回落 {end} 收盘价）")
        elif days > max_stale_days:
            reasons.append(f"{sym} K 线停在 {end}（落后 {days} 天）")
    valid = [v["stale_days"] for v in per_symbol.values() if v["stale_days"] is not None]
    return {
        "degraded": bool(reasons),
        "stale_days": max(valid) if valid else None,
        "max_stale_days": max_stale_days,
        "per_symbol": per_symbol,
        "reason": "；".join(reasons),
    }


def build_decision(signal_markets: Dict[str, dict], assets: Dict[str, dict], model: dict, month_status: dict, user_amount: Optional[float]) -> dict:
    """4/5/6 一体化：评分 → 部署系数 × 节奏系数 → 金额；评分 → 权重倾斜 → 比例。"""
    w = model.get("score_weights", {})
    if not isinstance(w, dict):
        w = {}
    scores = {key: asset_score(signal_markets.get(market_symbol_for_asset(key, info)) or {}, w) for key, info in assets.items()}
    remaining_budget = float(month_status.get("remaining_budget_rmb", 0.0))
    available_pool = float(month_status.get("available_pool_rmb", remaining_budget))
    base = float(user_amount) if user_amount is not None else float(month_status.get("paced_amount_rmb", 0.0))
    amount_source = "user_input" if user_amount is not None else "model"

    equity_keys = [k for k in assets if k != "gold"]
    equity_scores = [scores[k]["score"] for k in equity_keys if scores[k].get("score") is not None]
    if not equity_scores:
        return {
            "scores": scores, "weights": neutral_weights(assets),
            "level_label": "数据不可用", "deploy_multiplier": 0.0,
            "equity_opportunity": None, "base_amount_rmb": base, "suggested_amount_rmb": 0.0,
            "amount_source": amount_source, "reason": "行情数据不可用，延后观察",
        }

    opp = sum(equity_scores) / len(equity_scores)
    deploy_mult = clip(1.0 + float(model.get("deploy_gain", 1.1)) * opp, 0.0, float(model.get("deploy_max", 1.8)))

    skip_below = float(model.get("skip_below", 0.15))
    amount = 0.0 if deploy_mult < skip_below else round(base * deploy_mult, 2)
    amount = min(amount, available_pool)
    release_active = month_status.get("month_end_release_active")
    if release_active and amount > 0:
        amount = min(max(amount, float(month_status.get("paced_amount_rmb", 0.0))), available_pool)

    label = level_label(deploy_mult)
    parts = []
    for key, info in assets.items():
        s = scores.get(key) or {}
        if s.get("score") is None:
            continue
        contribs = {
            "回撤价值": w.get("value", 0.50) * s["value"],
            "趋势": w.get("trend", 0.25) * s["trend"],
            "动量": w.get("momentum", 0.15) * s["momentum"],
            "过热": -(w.get("heat", 0.45) * s["heat"] + w.get("heat_quad", 0.20) * s["heat"] * s["heat"]),
            "波动": -w.get("volatility", 0.15) * s["vol_penalty"],
        }
        top = sorted(contribs.items(), key=lambda kv: -abs(kv[1]))[:3]
        parts.append(f"{info.get('name_cn', key)} 评分 {s['score']:+.2f}（" + "、".join(f"{k}{v:+.2f}" for k, v in top) + "）")
    reason = "；".join(parts) + f"。权益综合 {opp:+.2f} → 部署系数 {deploy_mult:.2f}；本次基准 {base:.0f}（可用池 {available_pool:.0f} ÷ 剩余交易日 {month_status.get('remaining_trading_days', '?')}）"
    if release_active:
        if amount > 0:
            reason += "；月末释放中"
        else:
            reason += f"；月末释放窗口内本月可用池 {available_pool:.0f} RMB 未投出，持续跳过将有结余"
    if available_pool <= 0 and amount == 0 and deploy_mult >= skip_below:
        reason += "；本月可用池已用完，仅观察"

    return {
        "scores": scores,
        "weights": score_based_weights(scores, assets, model),
        "level_label": label,
        "deploy_multiplier": round(deploy_mult, 4),
        "equity_opportunity": round(opp, 4),
        "base_amount_rmb": base,
        "available_pool_rmb": round(available_pool, 2),
        "suggested_amount_rmb": round(amount, 2),
        "amount_source": amount_source,
        "reason": reason,
    }

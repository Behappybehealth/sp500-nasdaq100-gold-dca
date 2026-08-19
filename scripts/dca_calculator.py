#!/usr/bin/env python3
"""SP500 / Nasdaq100 / Gold dynamic DCA helper."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


DEFAULT_CONFIG = {
    "monthly_budget_rmb": 30000,
    "default_trade_amount_rmb": 5000,
    "assets": {
        "sp500": {"name_cn": "标普500", "symbol": "SPY", "index_symbol": "^GSPC", "neutral_weight": 0.35, "min_weight": 0.20, "max_weight": 0.55},
        "nasdaq100": {"name_cn": "纳指100", "symbol": "QQQ", "index_symbol": "^NDX", "neutral_weight": 0.45, "min_weight": 0.30, "max_weight": 0.70},
        "gold": {"name_cn": "黄金", "symbol": "XAUT", "index_symbol": "GC=F", "neutral_weight": 0.20, "min_weight": 0.10, "max_weight": 0.30},
    },
}

_BIZ_TZ = timezone(timedelta(hours=8))  # 业务时区：Asia/Shanghai（中国无夏令时，固定 +8 零依赖）


def biz_today() -> date:
    """业务"今天"的全链路唯一定义：Asia/Shanghai 自然日。

    Cloud 容器时区是 UTC——直接 date.today() 会让北京时间 00:00–07:59 的
    "今天"错成昨天。src/dates.py 持同规则实现（子进程隔离，两处必须同改）。
    """
    return datetime.now(_BIZ_TZ).date()


def is_iso_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


@dataclass
class Transaction:
    date: str
    action: str
    asset: str
    symbol: str
    currency: str
    amount_rmb: float
    price: float
    shares: float
    fee_rmb: float
    fx_rate: float
    notes: str


def clone_default_config() -> dict:
    return json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))


def read_json(path: Path) -> dict:
    config = clone_default_config()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        config.update(loaded)
    return config


def resolve_monthly_budget(config: dict, base_dir: Path, today: date, record_dir: Optional[Path] = None) -> tuple:
    """每月预算：budget_overrides.json 按月起算持久生效
    （{"2026-08": 45000} 表示 2026-08 起每月 45000，直到更新的月份键出现）。
    record_dir 指定记账数据目录（多用户模式为 data/users/<user>/），缺省读 base_dir/data/。
    返回 (effective_budget, source)。"""
    default = float(config.get("monthly_budget_rmb", DEFAULT_CONFIG["monthly_budget_rmb"]))
    month = today.strftime("%Y-%m")
    ov_dir = record_dir if record_dir is not None else base_dir / "data"
    try:
        overrides = json.loads((ov_dir / "budget_overrides.json").read_text(encoding="utf-8"))
    except Exception:
        return default, "default"
    keys = [k for k in overrides if isinstance(k, str) and k <= month]
    if not keys:
        return default, "default"
    latest = max(keys)
    try:
        return float(overrides[latest]), f"override:{latest}"
    except (TypeError, ValueError):
        return default, "default"


def as_float(value: str, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except ValueError:
        return default


def read_transactions(path: Path) -> List[Transaction]:
    if not path.exists():
        return []
    rows: List[Transaction] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                Transaction(
                    date=row.get("date", ""),
                    action=row.get("action", ""),
                    asset=row.get("asset", ""),
                    symbol=row.get("symbol", ""),
                    currency=row.get("currency", ""),
                    amount_rmb=as_float(row.get("amount_rmb", "0")),
                    price=as_float(row.get("price", "0")),
                    shares=as_float(row.get("shares", "0")),
                    fee_rmb=as_float(row.get("fee_rmb", "0")),
                    fx_rate=as_float(row.get("fx_rate", "1"), 1.0),
                    notes=row.get("notes", ""),
                )
            )
    return rows


def read_observations(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_last_observation(path: Path) -> Optional[dict]:
    rows = read_observations(path)
    return rows[-1] if rows else None


def trading_days_in_month(today: date, start: Optional[date] = None) -> tuple:
    """以周一~周五近似交易日（未剔除节假日，误差约 1 天/月）。

    返回 (当月总数, 含今天在内的剩余数, start 之前的交易日数)。
    start 之前的份额视为作废（月中才启动定投时不追补）。
    """
    first = date(today.year, today.month, 1)
    first_next = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
    total = 0
    remaining = 0
    before_start = 0
    d = first
    while d < first_next:
        if d.weekday() < 5:
            total += 1
            if start and d < start:
                before_start += 1
            if d >= today:
                remaining += 1
        d += timedelta(days=1)
    return total, max(1, remaining), before_start


def monthly_budget_status(transactions: List[Transaction], monthly_budget: float, today: date, interval_days: int = 1, release_window_days: int = 7, month_start_date: Optional[str] = None) -> dict:
    month_prefix = today.strftime("%Y-%m")
    invested_this_month = sum(
        tx.amount_rmb
        for tx in transactions
        if tx.action != "sell" and tx.date.startswith(month_prefix)
    )
    first_next_month = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
    remaining_days = max(1, (first_next_month - today).days)
    start = date.fromisoformat(month_start_date) if month_start_date else today
    total_td, remaining_td, td_before_start = trading_days_in_month(today, start)
    # 作废份额：仅策略启动前已过去的交易日；启动后跳过的日子不作废，自动摊入后续
    forfeited = monthly_budget * td_before_start / total_td if total_td else 0.0
    remaining_budget = max(0.0, monthly_budget - invested_this_month)
    available_pool = max(0.0, monthly_budget - invested_this_month - forfeited)
    remaining_opportunities = max(1, math.ceil(remaining_td / max(1, interval_days)))
    paced_amount = available_pool / remaining_td if available_pool > 0 else 0.0
    release_active = available_pool > 0 and remaining_days <= release_window_days
    return {
        "month": month_prefix,
        "monthly_budget_rmb": monthly_budget,
        "invested_this_month_rmb": invested_this_month,
        "forfeited_rmb": round(forfeited, 2),
        "remaining_budget_rmb": remaining_budget,
        "total_trading_days_in_month": total_td,
        "remaining_trading_days": remaining_td,
        "month_start_date": start.isoformat(),
        "available_pool_rmb": round(available_pool, 2),
        "remaining_days_in_month": remaining_days,
        "daily_reference_rmb": round(paced_amount, 2),
        "trade_interval_days": interval_days,
        "remaining_trade_opportunities": remaining_opportunities,
        "paced_amount_rmb": round(paced_amount, 2),
        "month_end_release_active": release_active,
        "month_end_release_window_days": release_window_days,
        "month_end_release_note": (
            f"月末释放已触发：本月还剩 {remaining_td} 个交易日，"
            f"每日需投约 {paced_amount:.0f} RMB 才能用完可用池 {available_pool:.0f} RMB"
            if release_active else ""
        ),
    }


def fetch_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 dca-calculator"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_chart(symbol: str, range_: str = "10y", interval: str = "1d", period1: Optional[date] = None, period2: Optional[date] = None) -> Dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    if period1 is not None and period2 is not None:
        p1 = int(datetime(period1.year, period1.month, period1.day, tzinfo=timezone.utc).timestamp())
        p2 = int(datetime(period2.year, period2.month, period2.day, tzinfo=timezone.utc).timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?period1={p1}&period2={p2}&interval={interval}&includePrePost=false"
    else:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_}&interval={interval}&includePrePost=false"
    data = fetch_json(url)
    chart = data.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No chart data returned for {symbol}")
    return results[0]


def metrics_from_closes(closes: List[float], latest: float, history_start: str, history_end: str) -> dict:
    metrics = {"latest_price": latest, "history_start": history_start, "history_end": history_end}
    for n in [1, 5, 20, 60, 120, 252]:
        if len(closes) > n:
            metrics[f"return_{n}d"] = closes[-1] / closes[-1 - n] - 1
    for n in [20, 60, 120, 252]:
        if len(closes) >= n:
            ma = sum(closes[-n:]) / n
            metrics[f"ma_{n}"] = ma
            metrics[f"ma_{n}_deviation"] = latest / ma - 1 if ma else None
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    for n in [20, 60]:
        if len(returns) >= n:
            recent = returns[-n:]
            mean = sum(recent) / len(recent)
            var = sum((x - mean) ** 2 for x in recent) / (len(recent) - 1)
            metrics[f"vol_{n}d_annualized"] = math.sqrt(var) * math.sqrt(252)
    if len(returns) >= 14:
        recent = returns[-14:]
        avg_gain = sum(x for x in recent if x > 0) / 14
        avg_loss = -sum(x for x in recent if x < 0) / 14
        metrics["rsi_14"] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    if len(closes) >= 252:
        last_year = closes[-252:]
        high = max(last_year)
        low = min(last_year)
        metrics["drawdown_from_252d_high"] = latest / high - 1 if high else None
        metrics["position_in_252d_range"] = (latest - low) / (high - low) if high > low else None
    return metrics


def pairs_from_chart_result(result: Dict[str, Any], allow_empty: bool = False) -> tuple:
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    raw_closes = quote.get("close", [])
    pairs = []
    for ts, close in zip(timestamps, raw_closes):
        if close is not None and close > 0:
            pairs.append((date.fromtimestamp(ts).isoformat(), float(close)))
    if not pairs and not allow_empty:
        raise RuntimeError("empty history")
    latest_raw = meta.get("regularMarketPrice")
    latest = float(latest_raw) if latest_raw else None
    return pairs, latest, meta


def sanitize_symbol(symbol: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in symbol)


def cache_file_for(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / f"{sanitize_symbol(symbol)}.csv"


def load_cached_closes(path: Path) -> Dict[str, float]:
    closes: Dict[str, float] = {}
    if not path.exists():
        return closes
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            day = row.get("date", "")
            close = as_float(row.get("close", "0"))
            if day and close > 0:
                closes[day] = close
    return closes


def close_at_or_before(closes: Dict[str, float], day: str) -> Optional[float]:
    eligible = [d for d in closes if d <= day]
    return closes[max(eligible)] if eligible else None


def save_cached_closes(path: Path, closes: Dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "close"])
        for day in sorted(closes):
            writer.writerow([day, f"{closes[day]:.6f}"])


def get_symbol_history(symbol: str, years: int, cache_dir: Optional[Path]) -> dict:
    today = biz_today()
    cache_path = cache_file_for(cache_dir, symbol) if cache_dir else None
    cached = load_cached_closes(cache_path) if cache_path else {}
    latest: Optional[float] = None
    currency = None
    data_source = ""
    warning = ""
    meta: Dict[str, Any] = {}

    if cached:
        last_cached = date.fromisoformat(max(cached))
        try:
            result = fetch_chart(symbol, period1=last_cached, period2=today + timedelta(days=1))
            pairs, latest, meta = pairs_from_chart_result(result, allow_empty=True)
            for day, close in pairs:
                cached[day] = close
            if cache_path:
                save_cached_closes(cache_path, cached)
            currency = meta.get("currency")
            data_source = "cache+yahoo_chart_incremental"
        except Exception as exc:  # network/data dependent
            warning = f"incremental fetch failed, using cache as of {max(cached)}: {exc}"
            data_source = "cache_stale"
    else:
        try:
            result = fetch_chart(symbol, range_=f"{years}y")
            pairs, latest, meta = pairs_from_chart_result(result)
            for day, close in pairs:
                cached[day] = close
            if cache_path:
                save_cached_closes(cache_path, cached)
            currency = meta.get("currency")
            data_source = "yahoo_chart_full+cache"
        except Exception as chart_exc:  # network/data dependent
            try:
                import yfinance as yf  # type: ignore
                hist = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=True)
                if hist.empty:
                    raise RuntimeError("empty history")
                for idx, close in zip(hist.index, hist["Close"].tolist()):
                    if close and close > 0:
                        cached[str(idx.date())] = float(close)
                data_source = "yfinance_full_no_cache"
                warning = f"yahoo_chart failed ({chart_exc}); yfinance result not written to cache"
            except Exception as yf_exc:  # pragma: no cover - network/data dependent
                return {"error": f"yahoo_chart: {chart_exc}; yfinance: {yf_exc}"}

    days = sorted(cached)
    closes = [cached[d] for d in days]
    if latest is None:
        latest = closes[-1]
    # 不追加 latest 到序列：避免实时价与最后一根K线收盘价近似时产生重复点，
    # 否则 day_change / return_1d 会恒为 0。
    metrics = metrics_from_closes(closes, latest, days[0], days[-1])
    # 日涨跌幅 = 最新价 vs 前一交易日收盘：盘中（最后一根为当日未完结K线）时 closes[-2] 即昨收；
    # 收盘后（最新价==最后收盘价）时即最近一个完整交易日的涨跌，不会出现恒为 0 的情况
    previous_close = closes[-2] if len(closes) > 1 else closes[-1]
    metrics["previous_close"] = previous_close
    metrics["day_change"] = latest / previous_close - 1 if previous_close else None
    metrics["currency"] = currency
    metrics["data_source"] = data_source
    metrics["history_points"] = len(closes)
    quote_ts = meta.get("regularMarketTime")
    if quote_ts:
        metrics["quote_time"] = int(quote_ts)
    if cache_path:
        metrics["cache_file"] = str(cache_path)
    if warning:
        metrics["cache_warning"] = warning
    return metrics


def fetch_history(symbols: Iterable[str], years: int = 10, cache_dir: Optional[Path] = None) -> Dict[str, dict]:
    return {symbol: get_symbol_history(symbol, years, cache_dir) for symbol in symbols}


def fetch_usdcny() -> Optional[float]:
    """USD/CNY 实时报价；抓不到返回 None（汇率是变量不是常量——绝不静默回落到写死的数）。"""
    try:
        result = fetch_chart("CNY=X", range_="5d")
        meta = result.get("meta", {})
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = [float(x) for x in quote.get("close", []) if x is not None and x > 0]
        return float(meta.get("regularMarketPrice") or closes[-1])
    except Exception:
        return None


def fetch_usdtusd() -> Optional[float]:
    """USDT/USD 报价；U 本位资产的人民币估值用 USDCNY × USDTUSD。抓不到返回 None。"""
    try:
        result = fetch_chart("USDT-USD", range_="5d")
        meta = result.get("meta", {})
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = [float(x) for x in quote.get("close", []) if x is not None and x > 0]
        return float(meta.get("regularMarketPrice") or closes[-1])
    except Exception:
        return None


_FX_LAST_NAME = "fx_last.json"


def load_fx_last(base_dir: Path) -> dict:
    """上次抓取成功的汇率（分字段 {"value", "fetched_at"}）；缺失/损坏返回 {}。"""
    try:
        data = json.loads((base_dir / "data" / _FX_LAST_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_fx_last(base_dir: Path, usdcny: Optional[float], usdtusd: Optional[float]) -> None:
    """把本次抓到的实时汇率并入 fx_last.json：只覆写抓成功的字段，另一字段保留上次值。"""
    if usdcny is None and usdtusd is None:
        return
    last = load_fx_last(base_dir)
    now = time.time()
    if usdcny is not None:
        last["usdcny"] = {"value": usdcny, "fetched_at": now}
    if usdtusd is not None:
        last["usdtusd"] = {"value": usdtusd, "fetched_at": now}
    try:
        (base_dir / "data" / _FX_LAST_NAME).write_text(
            json.dumps(last, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # 兜底缓存写失败不影响本次输出


def _fx_entry(live_value: Optional[float], last_entry, now: float) -> dict:
    """单汇率三件套 {value, live, as_of}：live=True 本次实时抓到（as_of=抓取时刻）；
    否则回落到 fx_last.json 的上次成功值（live=False，as_of=当时抓取时刻）；
    连上次值都没有 → value=None（估值层据此置空，绝不编一个数）。"""
    if live_value is not None:
        return {"value": live_value, "live": True, "as_of": now}
    if isinstance(last_entry, dict) and isinstance(last_entry.get("value"), (int, float)):
        return {"value": float(last_entry["value"]), "live": False, "as_of": last_entry.get("fetched_at")}
    return {"value": None, "live": False, "as_of": None}


_SNAPSHOT_NAME = "quote_snapshot.json"


def load_quote_snapshot(base_dir: Path, ttl_s: int) -> Optional[dict]:
    """读取 TTL 内的行情快照（上一次运行抓取的 markets/汇率），供短时间内的连续调用直接复用。

    过期、缺失或文件损坏一律返回 None，调用方退化为正常抓取。
    """
    if ttl_s <= 0:
        return None
    try:
        snap = json.loads((base_dir / "data" / _SNAPSHOT_NAME).read_text(encoding="utf-8"))
        age = time.time() - float(snap["fetched_at"])
        if 0 <= age < ttl_s and isinstance(snap.get("markets"), dict):
            snap["age_s"] = int(age)
            return snap
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def save_quote_snapshot(base_dir: Path, markets: Dict[str, dict], usdcny: Optional[float], usdtusd: Optional[float], fx: dict) -> None:
    """把本次行情抓取结果落盘为快照。任一标的带 error 时不存——失败结果不该被冻结一整个 TTL。"""
    if any(isinstance(m, dict) and m.get("error") for m in markets.values()):
        return
    payload = {"fetched_at": time.time(), "markets": markets, "usdcny": usdcny, "usdtusd": usdtusd, "fx": fx}
    try:
        (base_dir / "data" / _SNAPSHOT_NAME).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 快照只是加速缓存，写失败不影响本次输出


def market_symbol_for_asset(asset_key: str, info: dict) -> str:
    return info.get("index_symbol") or info.get("symbol") or asset_key


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
    for _ in range(3):
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}
        weights = {
            k: min(max(v, float(assets[k].get("min_weight", 0.0))), float(assets[k].get("max_weight", 1.0)))
            for k, v in weights.items()
        }
    total = sum(weights.values()) or 1.0
    return {k: v / total for k, v in weights.items()}


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


def render_wide_table(result: dict, transactions: List[Transaction], assets: Dict[str, dict]) -> str:
    def money(v: Optional[float]) -> str:
        return "暂无" if v is None else f"{v:,.2f}"

    def pct(v: Optional[float]) -> str:
        return "暂无" if v is None else f"{v * 100:.2f}%"

    def num(v: Optional[float]) -> str:
        return "暂无" if v is None else f"{v:,.4f}"

    def xirr_cell(value: Optional[float], days: Optional[int]) -> str:
        if value is None:
            return "暂无"
        if days is not None and days < 30:
            return "期短不年化"
        return f"{value * 100:.2f}%"

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

    last_by_asset: Dict[str, dict] = {}
    last_total: Optional[float] = None
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
    asset_dates: Dict[str, set] = {}
    for tx in transactions:
        asset_dates.setdefault(tx.asset, set()).add(tx.date)

    header = ["层级", "日期", "期数", "资产", "上一条记录类型", "上一条投入金额RMB", "本月已投RMB", "自然月剩余预算RMB", "累计投入RMB", "持仓份额", "最新价格", "当前估值RMB", "未实现盈亏RMB", "收益率", "年化XIRR", "组合权重", "今日建议金额RMB", "今日建议比例", "备注"]
    rows: List[list] = []
    positions = {p.get("asset"): p for p in portfolio.get("positions", [])}
    rows.append([
        "组合汇总", today, str(periods), "组合", last_type, money(last_total),
        money(invested_month), money(remaining_budget),
        money(portfolio.get("total_invested_rmb") or 0.0), "—", "—",
        money(portfolio.get("current_value_rmb") or 0.0), money(portfolio.get("unrealized_pnl_rmb") or 0.0),
        pct(portfolio.get("return_rate")), xirr_cell(portfolio.get("xirr"), portfolio.get("xirr_period_days")), "100.00%",
        money(total_suggested), "100.00%",
        f"{action.get('level_label', action.get('level', ''))}：{action.get('reason', '')}{release_note}",
    ])
    for key, info in assets.items():
        pos = positions.get(key)
        symbol = market_symbol_for_asset(key, info)
        m = markets.get(symbol) or {}
        w = weights.get(key, 0.0)
        rec = last_by_asset.get(key)
        sc = (scores_map.get(key) or {}).get("score")
        note = asset_note(m) + (f"；评分 {sc:+.2f}" if sc is not None else "")
        rows.append([
            "资产", today, str(len(asset_dates.get(key, set()))), info.get("name_cn", key),
            (rec.get("action") if rec else "暂无"),
            money(as_float(str(rec.get("amount_rmb", "0"))) if rec else None),
            money(invested_month), money(remaining_budget),
            money(pos.get("invested_rmb") if pos else 0.0),
            num(pos.get("shares") if pos else 0.0),
            money(m.get("latest_price")),
            money(pos.get("current_value_rmb") if pos else 0.0),
            money(pos.get("unrealized_pnl_rmb") if pos else 0.0),
            pct(pos.get("return_rate") if pos else None),
            xirr_cell(pos.get("xirr") if pos else None, pos.get("xirr_period_days") if pos else None),
            pct(pos.get("portfolio_weight") if pos else None),
            money(round(total_suggested * w, 2)),
            f"{w * 100:.1f}%",
            note,
        ])
    rows.append([
        "现金/待投", today, "—", "现金/待投预算", "暂无", "暂无",
        money(invested_month), money(remaining_budget),
        "0.00", "—", "—", money(remaining_budget), "—", "—", "—", "—",
        money(max(0.0, remaining_budget - total_suggested)), "—",
        "自然月剩余预算",
    ])
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", type=float, default=None, help="本次基准金额RMB；不指定时按行情档位阶梯 0/1500/3000/5000/7000 决定")
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history-years", type=int, default=10)
    parser.add_argument("--user", default=None, help="多用户模式：记账数据从 data/users/<user>/ 读取（config 与行情缓存保持共享）")
    parser.add_argument("--snapshot-ttl", type=int, default=600, help="行情快照复用窗口（秒）：TTL 内的连续运行直接复用上一次的抓取结果；0 = 每次全抓")
    args = parser.parse_args()

    if args.user and ("/" in args.user or "\\" in args.user or ".." in args.user):
        parser.error("非法用户名：不允许包含路径分隔符或 '..'")

    base_dir = args.base_dir
    config = read_json(base_dir / "data" / "config.json")
    # 多用户模式：记账数据按用户分目录，行情缓存/策略配置保持共享
    record_dir = base_dir / "data" / "users" / args.user if args.user else base_dir / "data"
    transactions = read_transactions(record_dir / "transactions.csv")
    # 坏日期防御（如 2026/08/17）：静默吞掉会让这笔钱逃出月度预算核算——
    # 剔出计算并在输出的 invalid_transactions 里点名，写入侧（records/dca_action）另有拒收
    invalid_transactions = [
        {"date": tx.date, "asset": tx.asset, "amount_rmb": tx.amount_rmb}
        for tx in transactions
        if not is_iso_date(tx.date)
    ]
    if invalid_transactions:
        transactions = [tx for tx in transactions if is_iso_date(tx.date)]
    observations = read_observations(record_dir / "observations.csv")
    last_observation = observations[-1] if observations else None
    assets = config.get("assets", DEFAULT_CONFIG["assets"])
    symbols = []
    for info in assets.values():
        candidates = [info.get("index_symbol")]
        if info.get("fetch_trade_symbol", True):
            candidates.append(info.get("symbol"))
        candidates.extend(info.get("price_proxy_symbols", []))
        for s in candidates:
            if s and s not in symbols:
                symbols.append(s)
    cache_cfg = config.get("cache", {})
    if not isinstance(cache_cfg, dict):
        cache_cfg = {}
    cache_dir = None
    if cache_cfg.get("enabled", True):
        cache_dir = base_dir / str(cache_cfg.get("dir", "data/market_history"))
    snapshot = load_quote_snapshot(base_dir, args.snapshot_ttl)
    if snapshot is not None:
        # 快照命中的本次运行跳过行情抓取与缓存增量更新；下次冷跑会自动追平缓存
        markets = snapshot["markets"]
        usdcny = snapshot["usdcny"]
        usdtusd = snapshot["usdtusd"]
        fx = snapshot.get("fx")
        if not isinstance(fx, dict) or "usdcny" not in fx:
            # 兼容旧格式快照（无 fx 段）：彼时汇率必有值（旧代码常量兜底），视同实时口径重建
            fx = {
                "usdcny": {"value": usdcny, "live": True, "as_of": snapshot["fetched_at"]},
                "usdtusd": {"value": usdtusd, "live": True, "as_of": snapshot["fetched_at"]},
            }
        snapshot_info = {"used": True, "age_s": snapshot["age_s"], "ttl_s": args.snapshot_ttl}
    else:
        markets = fetch_history(symbols, args.history_years, cache_dir)
        now = time.time()
        usdcny_live = fetch_usdcny()
        usdtusd_live = fetch_usdtusd()
        save_fx_last(base_dir, usdcny_live, usdtusd_live)
        last_fx = load_fx_last(base_dir)
        fx = {
            "usdcny": _fx_entry(usdcny_live, last_fx.get("usdcny"), now),
            "usdtusd": _fx_entry(usdtusd_live, last_fx.get("usdtusd"), now),
        }
        usdcny = fx["usdcny"]["value"]
        usdtusd = fx["usdtusd"]["value"]
        save_quote_snapshot(base_dir, markets, usdcny, usdtusd, fx)
        snapshot_info = {"used": False, "age_s": None, "ttl_s": args.snapshot_ttl}
    usdtcny = round(usdcny * usdtusd, 4) if (usdcny is not None and usdtusd is not None) else None
    interval_cfg = config.get("preferred_trade_interval_days", [2, 3])
    if isinstance(interval_cfg, list) and interval_cfg:
        interval_days = int(max(interval_cfg))
    else:
        interval_days = int(interval_cfg or 3)
    release_cfg = config.get("month_end_release", {})
    if not isinstance(release_cfg, dict):
        release_cfg = {}
    release_window_days = int(release_cfg.get("window_days", 7)) if release_cfg.get("enabled", True) else 0
    month_prefix = biz_today().strftime("%Y-%m")
    month_dates = [tx.date for tx in transactions if tx.date.startswith(month_prefix)]
    month_dates += [r.get("date", "") for r in observations if r.get("date", "").startswith(month_prefix)]
    month_start = min(month_dates) if month_dates else None
    monthly_budget, budget_source = resolve_monthly_budget(config, base_dir, biz_today(), record_dir)
    month_status = monthly_budget_status(
        transactions,
        monthly_budget,
        biz_today(),
        interval_days=interval_days,
        release_window_days=release_window_days,
        month_start_date=month_start,
    )
    month_status["budget_source"] = budget_source
    remaining_budget = month_status["remaining_budget_rmb"]
    # 信号与权重只看指数系列，避免指数+ETF 双系列重复计数
    signal_markets = {market_symbol_for_asset(key, info): markets.get(market_symbol_for_asset(key, info), {}) for key, info in assets.items()}
    model_cfg = dict(DEFAULT_MODEL)
    user_model = config.get("model", {})
    if isinstance(user_model, dict):
        model_cfg.update(user_model)
    decision = build_decision(signal_markets, assets, model_cfg, month_status, args.amount)
    weights = decision.get("weights", {})
    suggested_amount = decision.get("suggested_amount_rmb", 0.0)

    latest_records = []
    if transactions:
        last_date = max(tx.date for tx in transactions)
        latest_records.extend([tx.__dict__ for tx in transactions if tx.date == last_date])
    if last_observation:
        latest_records.append(last_observation)

    # 上期复盘后验数据：各资产自上次记录日（成交或观察）收盘价至今的涨跌
    last_record_date = max((tx.date for tx in transactions), default=None)
    if last_observation and last_observation.get("date"):
        obs_date = last_observation["date"]
        last_record_date = max(last_record_date, obs_date) if last_record_date else obs_date
    since_last_record: Dict[str, dict] = {}
    if last_record_date and cache_dir:
        for key, info in assets.items():
            sym = market_symbol_for_asset(key, info)
            closes = load_cached_closes(cache_file_for(cache_dir, sym))
            then = close_at_or_before(closes, last_record_date)
            latest = (markets.get(sym) or {}).get("latest_price")
            if then and latest:
                since_last_record[info.get("name_cn", key)] = {
                    "symbol": sym,
                    "last_record_date": last_record_date,
                    "price_then": then,
                    "latest_price": latest,
                    "change_pct": latest / then - 1,
                }

    result = {
        "as_of": str(biz_today()),
        "input_amount_rmb": args.amount,
        "effective_amount_rmb": round(suggested_amount, 2),
        "usdcny": usdcny,
        "usdtcny": usdtcny,
        "fx": fx,
        "monthly_budget_status": month_status,
        "config": config,
        "has_local_transactions": bool(transactions),
        "invalid_transactions": invalid_transactions,
        "last_records": latest_records,
        "since_last_record": since_last_record,
        "markets": markets,
        "quote_snapshot": snapshot_info,
        "portfolio": portfolio_summary(transactions, markets, assets, usdcny, usdtcny),
        "decision": decision,
        "suggested_weights": weights,
    }
    result["wide_table_markdown"] = render_wide_table(result, transactions, assets)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""行情抓取、缓存 I/O 与汇率获取。"""
from __future__ import annotations

import csv
import json
import math
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dca_types import as_float, biz_today, is_iso_date, utc_today


def fetch_json(url: str, timeout: int = 20, attempts: int = 3) -> dict[str, Any]:
    """取一次 Yahoo JSON；失败按 0.8s / 1.6s 退避重试，最后一次的异常原样抛出。

    单请求最坏 3×20s + 2.4s 退避 ≈ 62s——抓取已并发（fetch_history），
    总耗时取最大值而非求和，仍远低于 subprocess 的 180s 上限。
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 dca-calculator"})
    last_exc: Exception = RuntimeError("no attempt made")
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network/data dependent
            last_exc = exc
            if i < attempts - 1:
                time.sleep(0.8 * 2**i)
    raise last_exc


def fetch_chart(symbol: str, range_: str = "10y", interval: str = "1d", period1: date | None = None, period2: date | None = None) -> dict[str, Any]:
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


def metrics_from_closes(closes: list[float], latest: float, history_start: str, history_end: str) -> dict:
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


def pairs_from_chart_result(result: dict[str, Any]) -> tuple:
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    raw_closes = quote.get("close", [])
    pairs = []
    for ts, close in zip(timestamps, raw_closes):
        if close is not None and close > 0:
            # K 线日期按 UTC 解释，与落库闸（save_cached_closes 用 utc_today）同口径。
            # 用本机时区会让同一份数据在不同时区的机器上标成不同日期——XAUT 的
            # bar 时间戳正好是 UTC 00:00，负偏移时区下会整体错位到前一天。
            pairs.append((datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), float(close)))
    if not pairs:
        raise RuntimeError("empty history")
    latest_raw = meta.get("regularMarketPrice")
    latest = float(latest_raw) if latest_raw else None
    return pairs, latest, meta


def sanitize_symbol(symbol: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in symbol)


def cache_file_for(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / f"{sanitize_symbol(symbol)}.csv"


def load_cached_closes(path: Path) -> dict[str, float]:
    closes: dict[str, float] = {}
    if not path.exists():
        return closes
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            day = row.get("date", "")
            close = as_float(row.get("close", "0"))
            if day and close > 0:
                closes[day] = close
    return closes


def close_at_or_before(closes: dict[str, float], day: str) -> float | None:
    eligible = [d for d in closes if d <= day]
    return closes[max(eligible)] if eligible else None


_JUMP_WARN_PCT = 0.20


def save_cached_closes(path: Path, closes: dict[str, float]) -> list[str]:
    """把收盘序列落盘，三道护栏；返回 warning 列表（调用方透传到 JSON 输出）。

    1. **只写已收盘 K 线**：剔除 `date >= UTC 今天`。盘中价不进库——实时价走
       `meta.regularMarketPrice` 显示通道（冷热分离）。这是必需的，因为增量抓取
       每次都重抓前沿日，而一旦有更晚日期落库，脏值就永久冻结不再被重抓。
    2. **行数不减**：新序列比库里现有的短就拒写（防上游返回残缺数据把库削平）。
    3. **原子替换**：写临时文件再 os.replace，写盘中途挂掉不留残缺文件。

    ±20% 跳变只报 warning 不拦——1987 式真崩盘必须能落库。
    """
    warnings: list[str] = []
    cutoff = utc_today().isoformat()
    persistable = {d: c for d, c in closes.items() if d < cutoff}
    if not persistable:
        return [f"{path.name}: 无可落库数据（全部 K 线 >= UTC {cutoff}）"]
    existing = load_cached_closes(path)
    if len(persistable) < len(existing):
        return [f"{path.name}: 拒绝覆盖——新序列 {len(persistable)} 行 < 库内 {len(existing)} 行"]
    if persistable == existing:
        return warnings  # 无变化不碰文件（盘中/周末重跑的常态）
    days = sorted(persistable)
    prev_of = {cur: prev for prev, cur in zip(days, days[1:])}
    for day in sorted(d for d, c in persistable.items() if existing.get(d) != c):
        prev = prev_of.get(day)
        base = persistable.get(prev) if prev else None
        if base and base > 0 and abs(persistable[day] / base - 1) >= _JUMP_WARN_PCT:
            warnings.append(
                f"{path.name}: {day} 相对 {prev} 跳变 {persistable[day] / base - 1:+.1%}（已落库，请核对）"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "close"])
            for day in days:
                writer.writerow([day, f"{persistable[day]:.6f}"])
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        warnings.append(f"{path.name}: 落库失败 {exc}")
    return warnings


_LIVE_NAME = "market_live.json"
_REFETCH_LOOKBACK_DAYS = 5


def load_market_live(path: Path | None) -> dict[str, dict]:
    """读当日实时价缓存（`data/market_live.json`），只保留仍属"今天"的条目。

    存的是**当日尚未收盘的那根 K 线**：盘中记账时"我看到的那个价"需要一个落点，
    否则它只活在进程内存里，进程一退就没了——而 csv 按设计只收已收盘定稿值
    （见 save_cached_closes 第一道闸），当日值无处可去。

    日期 < UTC 今天的条目一律丢弃：那一天已经收盘，定稿值该由 csv 提供
    （增量抓取回退 _REFETCH_LOOKBACK_DAYS 天，会把它抓回来覆盖）。
    坏文件当空处理——这是缓存不是事实源，读不出来只是少一个当日点。
    """
    if not path or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cutoff = utc_today().isoformat()
    out: dict[str, dict] = {}
    for sym, entry in raw.items():
        bars = entry.get("bars") if isinstance(entry, dict) else None
        if not isinstance(bars, dict):
            continue
        fresh = {}
        for day, close in bars.items():
            value = as_float(close)
            if is_iso_date(str(day)) and str(day) >= cutoff and value > 0:
                fresh[str(day)] = value
        if fresh:
            out[sym] = {**entry, "bars": fresh}
    return out


def save_market_live(path: Path | None, entries: dict[str, dict]) -> None:
    """整体覆写当日实时价缓存。写失败静默——加速/留痕缓存，不是事实源。"""
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def split_live_bars(pairs: list[tuple]) -> dict[str, float]:
    """从本次抓到的 K 线里挑出"当日尚未收盘"那部分（`date >= UTC 今天`）。

    与 save_cached_closes 第一道闸是同一条界线：被它剔出 csv 的正是这些点，
    这里把它们接住。**日期用数据源自己的 bar 时间戳，不自己按"今天"推算** ——
    休市的标的不会开出今天的 bar，于是自然没有当日点（此时最新可得价就是上一
    收盘价，走 meta 的实时价通道），24 小时交易的标的才会有。
    """
    cutoff = utc_today().isoformat()
    return {day: close for day, close in pairs if day >= cutoff}


def merge_live_bars(closes: dict[str, float], live_bars: dict[str, float]) -> dict[str, float]:
    """把当日实时价并进收盘序列——**csv 有该日期则以 csv 为准**。

    定稿值一落库就自动顶掉临时值，不需要任何清理逻辑；反过来若让 live 覆盖
    csv，收盘后每次跑都会拿盘中价盖掉定稿价。
    """
    merged = dict(closes)
    for day, close in live_bars.items():
        merged.setdefault(day, close)
    return merged


def _yfinance_closes(symbol: str, years: int) -> dict[str, float]:
    """yfinance 兜底序列，**raw 口径**（auto_adjust=False，与 Chart 主路径一致）。

    口径必须钉死：一边复权一边不复权，拆股/分红日前后就会在同一份缓存里形成
    永久断点，跨断点的涨跌与回撤全错。本函数结果只供本次决策，不落库
    （落库口径单一由 Chart 路径负责，见 save_cached_closes）。
    """
    import yfinance as yf  # type: ignore

    hist = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=False)
    if hist.empty:
        raise RuntimeError("empty history")
    out: dict[str, float] = {}
    for idx, close in zip(hist.index, hist["Close"].tolist()):
        if close and close > 0:
            out[str(idx.date())] = float(close)
    if not out:
        raise RuntimeError("empty history")
    return out


def get_symbol_history(symbol: str, years: int, cache_dir: Path | None, live_entry: dict | None = None) -> dict:
    today = biz_today()
    cache_path = cache_file_for(cache_dir, symbol) if cache_dir else None
    cached = load_cached_closes(cache_path) if cache_path else {}  # 只含已收盘定稿值
    live_bars: dict[str, float] = dict((live_entry or {}).get("bars") or {})
    fresh_live: dict[str, float] | None = None  # 本次抓到的当日 bar；None=没抓到，别动存量
    latest: float | None = None
    currency = None
    data_source = ""
    warning = ""
    persist_warnings: list[str] = []
    meta: dict[str, Any] = {}

    if cached:
        last_cached = date.fromisoformat(max(cached))
        try:
            # period1 回退 5 天而不是只抓前沿那一天：数据源会给 close=null 的空洞
            # （实测 GC=F / XAUT 的 2026-08-19），也会事后改写已收盘的值（实测 XAUT
            # 08-17 +0.83%）。只抓前沿则一有更晚日期落库，那两种错就永久留在库里。
            # 回退不增加请求数，同一个 Chart 调用只是 range 大一点。
            result = fetch_chart(
                symbol,
                period1=last_cached - timedelta(days=_REFETCH_LOOKBACK_DAYS),
                period2=today + timedelta(days=1),
            )
            # 不允许空响应：period1 落在库内最后一天之前，正常必回 K 线；
            # 空返回是上游异常而不是"没有新数据"，静默当成功会让缓存无限期装死
            pairs, latest, meta = pairs_from_chart_result(result)
            for day, close in pairs:
                cached[day] = close
            live_bars = fresh_live = split_live_bars(pairs)
            if cache_path:
                persist_warnings = save_cached_closes(cache_path, cached)
            currency = meta.get("currency")
            data_source = "cache+yahoo_chart_incremental"
        except Exception as exc:  # network/data dependent
            try:
                # 有缓存也要试 yfinance：否则 Yahoo Chart 一挂就无声无息地一直吃旧缓存
                cached.update(_yfinance_closes(symbol, years))
                data_source = "yfinance_fallback+cache"
                warning = f"yahoo_chart 增量失败（{exc}）；已用 yfinance 兜底，结果不入库"
            except Exception as yf_exc:  # network/data dependent
                warning = f"抓取失败，用库存到 {max(cached)}：yahoo_chart: {exc}；yfinance: {yf_exc}"
                data_source = "cache_stale"
    else:
        try:
            result = fetch_chart(symbol, range_=f"{years}y")
            pairs, latest, meta = pairs_from_chart_result(result)
            for day, close in pairs:
                cached[day] = close
            live_bars = fresh_live = split_live_bars(pairs)
            if cache_path:
                persist_warnings = save_cached_closes(cache_path, cached)
            currency = meta.get("currency")
            data_source = "yahoo_chart_full+cache"
        except Exception as chart_exc:  # network/data dependent
            try:
                cached.update(_yfinance_closes(symbol, years))
                data_source = "yfinance_full_no_cache"
                warning = f"yahoo_chart 失败（{chart_exc}）；yfinance 结果不入库"
            except Exception as yf_exc:  # pragma: no cover - network/data dependent
                return {"error": f"yahoo_chart: {chart_exc}; yfinance: {yf_exc}"}

    series = merge_live_bars(cached, live_bars)
    days = sorted(series)
    closes = [series[d] for d in days]
    if latest is None:
        # 拿不到实时价就只能回落最后一根收盘——**必须留痕**，否则旧价冒充实时价
        # 一路走到金额上都没人看得见。新鲜度闸按这个标记拦（market_freshness）。
        latest = closes[-1]
        latest_source = "last_close"
    else:
        latest_source = "quote"
    # 不额外追加 latest 到序列：当日那根 K 线（若数据源已开出）已经由 live 合并进来，
    # 日期由数据源的 bar 时间戳决定；自己按"今天"造点会在休市时产生与昨天等值的
    # 重复点，把 day_change / return_1d 压成 0。
    metrics = metrics_from_closes(closes, latest, days[0], days[-1])
    # 日涨跌幅 = 最新价 vs 前一交易日收盘：盘中（最后一根为当日未完结K线）时 closes[-2] 即昨收；
    # 收盘后（最新价==最后收盘价）时即最近一个完整交易日的涨跌，不会出现恒为 0 的情况
    previous_close = closes[-2] if len(closes) > 1 else closes[-1]
    metrics["previous_close"] = previous_close
    metrics["day_change"] = latest / previous_close - 1 if previous_close else None
    metrics["latest_source"] = latest_source
    metrics["currency"] = currency
    metrics["data_source"] = data_source
    metrics["history_points"] = len(closes)
    quote_ts = meta.get("regularMarketTime")
    if quote_ts:
        metrics["quote_time"] = int(quote_ts)
    if cache_path:
        metrics["cache_file"] = str(cache_path)
    if warning:
        metrics["cache_warning"] = warning  # 语义=数据没更新到最新（UI 据此标黄）
    if persist_warnings:
        metrics["persist_warnings"] = persist_warnings  # 语义=落库护栏说了话，与新鲜度无关
    # 私有键：由 fetch_history 汇总后整体落盘，避免多线程抢同一个 json；出函数前被 pop。
    # 兜底路径给 None（没抓到当日 bar ≠ 当日 bar 不存在），存量当日值原样留着
    metrics["_live_bars"] = fresh_live
    return metrics


def fetch_history(
    symbols: Iterable[str],
    years: int = 10,
    cache_dir: Path | None = None,
    live_path: Path | None = None,
) -> dict[str, dict]:
    """并发抓取全部标的（原先串行 → 总耗时是求和，最坏 160s 紧贴 subprocess 的 180s 上限）。

    每个标的写自己的缓存文件、互不共享写入，因此并发安全；总耗时从"求和"变"取最大值"。
    单个标的抛异常收成 error 条目，不带走整批——降级展示优于整页失败。

    当日实时价的**读取**在线程里（只读，安全），**落盘**收到这里统一做一次：
    market_live.json 是一个全标的共用的文件，放线程里写会互相覆盖。
    """
    syms = list(symbols)
    live_all = load_market_live(live_path)

    def _one(sym: str) -> dict:
        try:
            return get_symbol_history(sym, years, cache_dir, live_all.get(sym))
        except Exception as exc:  # network/data dependent
            return {"error": f"{type(exc).__name__}: {exc}"}

    if len(syms) <= 1:
        out = {s: _one(s) for s in syms}
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(syms))) as pool:
            out = dict(zip(syms, pool.map(_one, syms)))

    updated = dict(live_all)
    changed = False
    for sym, metrics in out.items():
        bars = metrics.pop("_live_bars", None)
        if bars is None:  # 整体失败或走了兜底路径 → 本次没有当日 bar 的可信信息，不动存量
            continue
        changed = True
        if bars:
            updated[sym] = {
                "bars": bars,
                "quote_time": metrics.get("quote_time"),
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        else:
            updated.pop(sym, None)  # 数据源没开出当日 bar（休市）→ 没有当日值可存
    if changed and updated != live_all:
        save_market_live(live_path, updated)
    return out


def fetch_usdcny() -> float | None:
    """USD/CNY 实时报价；抓不到返回 None（汇率是变量不是常量——绝不静默回落到写死的数）。"""
    try:
        result = fetch_chart("CNY=X", range_="5d")
        meta = result.get("meta", {})
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = [float(x) for x in quote.get("close", []) if x is not None and x > 0]
        return float(meta.get("regularMarketPrice") or closes[-1])
    except Exception:
        return None


def fetch_usdtusd() -> float | None:
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


def save_fx_last(base_dir: Path, usdcny: float | None, usdtusd: float | None) -> None:
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


def _fx_entry(live_value: float | None, last_entry, now: float) -> dict:
    """单汇率三件套 {value, live, as_of}：live=True 本次实时抓到（as_of=抓取时刻）；
    否则回落到 fx_last.json 的上次成功值（live=False，as_of=当时抓取时刻）；
    连上次值都没有 → value=None（估值层据此置空，绝不编一个数）。"""
    if live_value is not None:
        return {"value": live_value, "live": True, "as_of": now}
    if isinstance(last_entry, dict) and isinstance(last_entry.get("value"), (int, float)):
        return {"value": float(last_entry["value"]), "live": False, "as_of": last_entry.get("fetched_at")}
    return {"value": None, "live": False, "as_of": None}


_SNAPSHOT_NAME = "quote_snapshot.json"


def load_quote_snapshot(base_dir: Path, ttl_s: int) -> dict | None:
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


def save_quote_snapshot(base_dir: Path, markets: dict[str, dict], usdcny: float | None, usdtusd: float | None, fx: dict) -> None:
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

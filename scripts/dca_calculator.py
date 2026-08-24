#!/usr/bin/env python3
"""SP500 / Nasdaq100 / Gold dynamic DCA helper.

引擎拆分为 5 个兄弟模块（线性依赖 DAG，无循环引用）：
  dca_types     — 数据结构、工具函数、记账数据加载
  dca_market    — 行情抓取、缓存 I/O、汇率获取
  dca_portfolio — XIRR 与组合持仓计算
  dca_scoring   — 评分模型与决策引擎
  dca_table     — 宽表结构化行与 markdown 渲染

本文件是薄入口：只保留 main() 与 stdout/stderr 编码修正；
所有公共符号通过 re-export 保持 `import dca_calculator as eng` 全部调用方零改动。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# --- re-exports（保 import dca_calculator as eng 的全部符号可用）---
from dca_types import (  # noqa: F401
    DEFAULT_CONFIG, Transaction, as_float, biz_today, clone_default_config,
    is_iso_date, monthly_budget_status, read_json, read_last_observation,
    read_observations, read_transactions, resolve_monthly_budget,
    trading_days_in_month, utc_today,
)
from dca_market import (  # noqa: F401
    _LIVE_NAME, _fx_entry, _yfinance_closes, cache_file_for, close_at_or_before,
    fetch_chart, fetch_history, fetch_json, fetch_usdcny, fetch_usdtusd,
    get_symbol_history, load_cached_closes, load_fx_last, load_market_live, load_quote_snapshot,
    market_symbol_for_asset, merge_live_bars, metrics_from_closes,
    pairs_from_chart_result, sanitize_symbol, save_cached_closes,
    save_fx_last, save_market_live, save_quote_snapshot, split_live_bars,
)
from dca_portfolio import portfolio_summary, xirr, xnpv  # noqa: F401
from dca_scoring import (  # noqa: F401
    DEFAULT_MODEL, asset_score, build_decision, clip, level_label,
    market_freshness, neutral_weights, score_based_weights,
)
from dca_table import (  # noqa: F401
    WIDE_TABLE_HEADER, asset_note, build_wide_rows, render_wide_table,
)


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
    live_path = None
    if cache_cfg.get("enabled", True):
        cache_dir = base_dir / str(cache_cfg.get("dir", "data/market_history"))
        live_path = base_dir / "data" / _LIVE_NAME  # 当日未收盘 bar 的落点（收盘序列仍归 csv）
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
        # 行情与两个汇率同波并发：总耗时 = 最慢的那一个，而不是 8 个请求求和
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_markets = pool.submit(fetch_history, symbols, args.history_years, cache_dir, live_path)
            f_usdcny = pool.submit(fetch_usdcny)
            f_usdtusd = pool.submit(fetch_usdtusd)
            markets = f_markets.result()
            usdcny_live = f_usdcny.result()
            usdtusd_live = f_usdtusd.result()
        now = time.time()
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
    # 新鲜度闸：拿不到实时价就不出金额（旧价算出的"今天买多少"比不给建议更危险）
    freshness = market_freshness(signal_markets, biz_today())
    if freshness["degraded"]:
        decision["suggested_amount_rmb"] = 0.0
        decision["level_label"] = "行情不可用·暂停出金额"
        decision["reason"] = (
            f"⚠️ 行情不可用于决策（{freshness['reason']}）"
            f" → 本次不出金额，只展示持仓。原评分供参考：{decision['reason']}"
        )
    decision["degraded"] = freshness["degraded"]
    decision["freshness"] = freshness
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
        # 当日 bar 一并合进来：last_record_date 落在今天时（今天记的账），"当时价"
        # 该是今天那根 bar，不是昨天的收盘——与评分序列同一条 merge 规则
        live_since = load_market_live(live_path)
        for key, info in assets.items():
            sym = market_symbol_for_asset(key, info)
            closes = merge_live_bars(
                load_cached_closes(cache_file_for(cache_dir, sym)),
                (live_since.get(sym) or {}).get("bars") or {},
            )
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
    result["wide_table_rows"] = build_wide_rows(result, transactions, assets)
    result["wide_table_markdown"] = render_wide_table(result, transactions, assets)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

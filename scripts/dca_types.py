#!/usr/bin/env python3
"""DCA 数据结构、工具函数与记账数据加载。"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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


def utc_today() -> date:
    """落库安全线用的 UTC 自然日（区别于业务"今天" biz_today）。

    UTC 午夜这一刻，美股（16:00 ET = UTC 20:00/21:00 收盘）、GC=F、24/7 的 XAUT
    都已走完前一个 UTC 日——一条规则覆盖全部标的，无需按标的维护收盘时刻表。
    """
    return datetime.now(timezone.utc).date()


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


def resolve_monthly_budget(config: dict, base_dir: Path, today: date, record_dir: Path | None = None) -> tuple:
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


def read_transactions(path: Path) -> list[Transaction]:
    if not path.exists():
        return []
    rows: list[Transaction] = []
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


def read_observations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_last_observation(path: Path) -> dict | None:
    rows = read_observations(path)
    return rows[-1] if rows else None


def trading_days_in_month(today: date, start: date | None = None) -> tuple:
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


def monthly_budget_status(transactions: list[Transaction], monthly_budget: float, today: date, interval_days: int = 1, release_window_days: int = 7, month_start_date: str | None = None) -> dict:
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

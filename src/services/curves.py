# -*- coding: utf-8 -*-
"""曲线与文件数据：JSON 加载 / 缓存行情序列 / 组合净值曲线。

显式收 paths 参数，不读 app.py 模块级全局。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from ..context import Paths
from ..market_cache import cache_file_for, load_cached_closes, close_at_or_before


def _load_json(path: Path):
    """读取 JSON 文件；损坏/读取失败返回 None，由调用方决定跳过展示。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


@st.cache_data(ttl=900, show_spinner=False)
def load_price_series(paths: Paths):
    """读取缓存行情，用于组合曲线与图表。"""
    cache_dir = paths.data_dir / "market_history"
    series = {}
    for sym in ["SPY", "QQQ", "XAUT-USD"]:
        closes = load_cached_closes(cache_file_for(cache_dir, sym))
        if closes:
            series[sym] = closes
    return series


def tx_csv_for(paths: Paths, user: str) -> Path:
    """当前用户的成交账本路径：与引擎 `--user` 同一条规则。

    单机模式（"local" 哨兵）读共享 `data/transactions.csv`；多用户模式读
    `data/users/<user>/transactions.csv`（sync_local 的落盘缓存，登录与每次写后刷新）。
    """
    if user == "local":
        return paths.tx_csv
    if "/" in user or "\\" in user or ".." in user:
        raise ValueError(f"非法用户名：{user!r}")
    return paths.data_dir / "users" / user / "transactions.csv"


def portfolio_curve(result: dict, paths: Paths, user: str):
    """按成交记录重建每日 投入 vs 市值 曲线。"""
    tx_csv = tx_csv_for(paths, user)
    if not tx_csv.exists() or tx_csv.stat().st_size == 0:
        return None
    rows = list(csv.DictReader(tx_csv.open("r", encoding="utf-8-sig")))
    if not rows:
        return None
    series = load_price_series(paths)
    asset_sym = {"sp500": "SPY", "nasdaq100": "QQQ", "gold": "XAUT-USD"}
    usdcny = result.get("usdcny")
    fx_map = {
        "sp500": usdcny,
        "nasdaq100": usdcny,
        "gold": result.get("usdtcny") or usdcny,
    }
    if any(v is None for v in fx_map.values()):
        return None  # 汇率不可用 → 估值曲线整体置空：残缺曲线比没有曲线更误导
    days = sorted({d for s in series.values() for d in s})
    first = min(r["date"] for r in rows)
    days = [d for d in days if d >= first]
    out = []
    shares = {"sp500": 0.0, "nasdaq100": 0.0, "gold": 0.0}
    invested = 0.0
    tx_by_date = {}
    for r in rows:
        tx_by_date.setdefault(r["date"], []).append(r)
    for d in days:
        for r in tx_by_date.get(d, []):
            a = r["asset"]
            sign = -1.0 if r["action"] == "sell" else 1.0
            try:
                shares[a] = shares.get(a, 0.0) + sign * float(r["shares"] or 0)
                invested += sign * float(r["amount_rmb"] or 0)
            except (TypeError, ValueError):
                continue  # 单笔坏行跳过，不拖垮整条曲线
        value = 0.0
        for a, sym in asset_sym.items():
            s = series.get(sym, {})
            px = close_at_or_before(s, d)
            if px and shares.get(a):
                value += shares[a] * px * fx_map[a]
        out.append(
            {"日期": d, "累计投入": round(invested, 0), "组合市值": round(value, 0)}
        )
    return pd.DataFrame(out).set_index("日期")

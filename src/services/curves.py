# -*- coding: utf-8 -*-
"""曲线与文件数据：JSON 加载 / 缓存行情序列 / 组合净值曲线。

BUG-020 刀 2 从 app.py:615-620、694-752 原样搬入；仅把模块级全局 CODE_DIR/DATA_DIR/TX_CSV 换成显式 paths 参数。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from ..context import Paths


def _load_json(path: Path):
    """读取 JSON 文件；损坏/读取失败返回 None，由调用方决定跳过展示。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


@st.cache_data(ttl=900, show_spinner=False)
def load_price_series(paths: Paths):
    """读取缓存行情，用于组合曲线与图表。"""
    scripts_dir = str(paths.code_dir / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import dca_calculator as dca  # type: ignore[import-not-found]  # scripts/ 运行时才加入 sys.path，静态分析解析不到

    cache_dir = paths.data_dir / "market_history"
    series = {}
    for sym in ["SPY", "QQQ", "XAUT-USD"]:
        closes = dca.load_cached_closes(dca.cache_file_for(cache_dir, sym))
        if closes:
            series[sym] = closes
    return series


def portfolio_curve(result: dict, paths: Paths):
    """按成交记录重建每日 投入 vs 市值 曲线。"""
    if not paths.tx_csv.exists() or paths.tx_csv.stat().st_size == 0:
        return None
    rows = list(csv.DictReader(paths.tx_csv.open("r", encoding="utf-8-sig")))
    if not rows:
        return None
    series = load_price_series(paths)
    asset_sym = {"sp500": "SPY", "nasdaq100": "QQQ", "gold": "XAUT-USD"}
    fx_map = {
        "sp500": result["usdcny"],
        "nasdaq100": result["usdcny"],
        "gold": result.get("usdtcny", result["usdcny"]),
    }
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
            eligible = [x for x in s if x <= d]
            if eligible and shares.get(a):
                value += shares[a] * s[max(eligible)] * fx_map[a]
        out.append(
            {"日期": d, "累计投入": round(invested, 0), "组合市值": round(value, 0)}
        )
    return pd.DataFrame(out).set_index("日期")

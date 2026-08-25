"""实时行情报价：东财 XAU 现货 + Yahoo BTC。

显式收 paths 参数，不读 app.py 模块级全局。
"""
from __future__ import annotations

import contextlib
import json
import subprocess

import streamlit as st

from ..context import Paths


@st.cache_data(ttl=900, show_spinner=False)
def fetch_xau_spot(paths: Paths):
    """黄金/美元现货（东财 122.XAU）。东财对 urllib 断连且偶发限流：
    curl 抓取 + 失败重试 + 落盘最近一次成功值兜底。返回 None 表示彻底失败。"""
    url = "https://push2.eastmoney.com/api/qt/stock/get?secid=122.XAU&fields=f43,f57,f58,f60,f169,f170,f86"
    for _ in range(3):
        try:
            out = subprocess.run(
                [
                    "curl",
                    "-s",
                    "--max-time",
                    "8",
                    "-H",
                    "User-Agent: Mozilla/5.0",
                    "-H",
                    "Referer: https://quote.eastmoney.com/",
                    url,
                ],
                capture_output=True,
                timeout=12,
                check=False,
            )
            d = json.loads(out.stdout.decode("utf-8")).get("data") or {}
            if d.get("f43"):
                rec = {
                    "price": d["f43"] / 100,
                    "chg_pct": d.get("f170", 0) / 10000,
                    "ts": d.get("f86"),
                }
                with contextlib.suppress(Exception):
                    (paths.data_dir / "xau_spot_last.json").write_text(
                        json.dumps(rec), encoding="utf-8"
                    )
                return rec
        except Exception:
            pass
    try:  # 用最后一次成功值兜底，标记缓存
        last = json.loads(
            (paths.data_dir / "xau_spot_last.json").read_text(encoding="utf-8")
        )
        last["stale"] = True
        return last
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_btc(paths: Paths):
    """比特币实时行情（Yahoo BTC-USD）。失败时落盘兜底 + 标注"更新失败，使用历史数据"。"""
    import urllib.request

    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?range=5d&interval=1d&includePrePost=false"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        r = (data.get("chart", {}).get("result") or [None])[0]
        if not r:
            return None
        meta = r.get("meta", {})
        price = meta.get("regularMarketPrice")
        if not price:
            return None
        prev = meta.get("chartPreviousClose") or price
        rec = {
            "price": float(price),
            "chg_pct": float(price) / float(prev) - 1,
            "ts": meta.get("regularMarketTime"),
        }
        with contextlib.suppress(Exception):
            (paths.data_dir / "btc_last.json").write_text(
                json.dumps(rec), encoding="utf-8"
            )
        return rec
    except Exception:
        pass
    try:  # 用最后一次成功值兜底，标记缓存 + 标注更新失败
        last = json.loads(
            (paths.data_dir / "btc_last.json").read_text(encoding="utf-8")
        )
        last["stale"] = True
        last["stale_label"] = "⚠️更新失败，使用历史数据"
        return last
    except Exception:
        return None

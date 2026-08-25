"""行情缓存读写：纯函数，无引擎 / Streamlit 依赖。

与 `scripts/dca_calculator.py` 里的同名函数保持一致（双实现，同 `biz_today()`
先例）——引擎是 stdlib-only 子进程，不能反向 import src/；Web 侧只需这几个
读缓存函数，不值得为此把 1400 行引擎拉进 sys.path。
"""
from __future__ import annotations

import csv
from pathlib import Path


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
            close = float(row.get("close", "0") or 0)
            if day and close > 0:
                closes[day] = close
    return closes


def close_at_or_before(closes: dict[str, float], day: str) -> float | None:
    eligible = [d for d in closes if d <= day]
    return closes[max(eligible)] if eligible else None

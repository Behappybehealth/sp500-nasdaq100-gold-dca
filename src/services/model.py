# -*- coding: utf-8 -*-
"""决策模型调用：subprocess 起 scripts/dca_calculator.py，收 JSON。

显式收 paths 参数，不读 app.py 模块级全局。
"""
from __future__ import annotations

import json
import subprocess
import sys

import pandas as pd
import streamlit as st

from ..context import Paths


@st.cache_data(ttl=900, show_spinner=False)  # 加载提示由下方自定义浮动组件负责
def run_model(amount: float | None, user: str, paths: Paths) -> dict:
    """user 必须进缓存键：否则 A 用户的结果会被 B 直接命中（串号）。"""
    cmd = [
        sys.executable,
        str(paths.code_dir / "scripts" / "dca_calculator.py"),
        "--base-dir",
        str(paths.base),
    ]
    if user != "local":  # 多用户模式：引擎从 data/users/<user>/ 读记账数据
        cmd += ["--user", user]
    if amount:
        cmd += ["--amount", str(amount)]
    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=180
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr or "calculator failed")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as _e:
        raise RuntimeError(f"计算器输出解析失败：{_e}") from None


def parse_wide_table(md: str) -> pd.DataFrame:
    lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
    return pd.DataFrame(rows[2:], columns=rows[0])

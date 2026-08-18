# -*- coding: utf-8 -*-
"""Tab6 策略说明：读 strategy/core-strategy.md 渲染（唯一事实源，BUG-026）。

BUG-020 刀 4 从 app.py:1156-1163 原样搬入；仅把全局 CODE_DIR 换成显式参数。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st


def render(tab, code_dir: Path):
    with tab:
        # 策略说明唯一事实源是 strategy/core-strategy.md（BUG-026：删掉内嵌副本，杜绝双份漂移）
        try:
            st.markdown(
                (code_dir / "strategy" / "core-strategy.md").read_text(encoding="utf-8")
            )
        except OSError as _e:
            st.error(f"策略说明文件读取失败：{_e}")

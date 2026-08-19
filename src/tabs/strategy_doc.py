# -*- coding: utf-8 -*-
"""Tab6 策略说明：读 strategy/core-strategy.md 渲染（唯一事实源，改文档即改页面）。"""
from __future__ import annotations

from pathlib import Path

import streamlit as st


def render(tab, code_dir: Path):
    with tab:
        # 策略说明唯一事实源是 strategy/core-strategy.md（只此一份，杜绝双份漂移）
        try:
            st.markdown(
                (code_dir / "strategy" / "core-strategy.md").read_text(encoding="utf-8")
            )
        except OSError as _e:
            st.error(f"策略说明文件读取失败：{_e}")

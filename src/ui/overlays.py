# -*- coding: utf-8 -*-
"""三个遮罩/加载组件：show_loading / show_sync_mask / show_auth_mask。

BUG-020 刀 3 从 app.py:218-266 原样搬入，零逻辑改动。
⚠️ 遮罩视觉正确性依赖 styles.py 里对应 CSS 类的不透明 background（详设 §6 的冻屏坑）。
"""
from __future__ import annotations

import streamlit as st


def show_loading(msg: str, sub: str = ""):
    """非阻塞浮动加载提示；返回 placeholder，完成后调 .empty()。"""
    ph = st.empty()
    ph.markdown(
        "<div class='dca-shimmer'></div>"
        "<div class='dca-loader'><div class='dca-ring'></div><div>"
        f"<div class='dca-loader-msg'>{msg}</div>"
        + (f"<div class='dca-loader-sub'>{sub}</div>" if sub else "")
        + "</div></div>",
        unsafe_allow_html=True,
    )
    return ph


def show_sync_mask(msg: str, sub: str = ""):
    """登录后整屏同步遮罩：盖住上一个 run 残留的登录页。"""
    ph = st.empty()
    ph.markdown(
        "<div class='dca-sync-mask'><div class='dca-sync-ring'></div>"
        f"<div class='dca-sync-msg'>{msg}</div>"
        + (f"<div class='dca-sync-sub'>{sub}</div>" if sub else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    return ph


def show_auth_mask(title: str, steps: list, ph=None):
    """登录中整屏遮罩：自带不透明深色背景+科技纹理（fz-bg 在遮罩内部），完全盖住残留登录页。
    steps: [(文案, 状态)]，状态 on=进行中 / done=完成 / off=待办。
    传入 ph（st.empty 占位）则原地更新卡片，用于推进分步进度。"""
    if ph is None:
        ph = st.empty()
    rows = "".join(
        f"<div class='dca-auth-step {state}'><span class='dot'></span>{label}</div>"
        for label, state in steps
    )
    ph.markdown(
        "<div class='dca-auth-mask'>"
        "<div class='fz-bg'><div class='fz-grid'></div><div class='fz-stars'></div>"
        "<div class='fz-stars s2'></div><div class='fz-orb o1'></div><div class='fz-orb o2'></div></div>"
        "<div class='dca-auth-card'>"
        "<div class='dca-auth-ring'></div>"
        f"<div class='dca-auth-title'>{title}</div>"
        f"<div class='dca-auth-steps'>{rows}</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )
    return ph

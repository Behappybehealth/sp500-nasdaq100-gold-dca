# -*- coding: utf-8 -*-
"""全局 CSS：登录页科技风背景、玻璃拟态登录卡、加载/遮罩组件样式。

BUG-020 刀 3 从 app.py:42-215 原样搬入（仅包成 inject_css()）。
⚠️ 遮罩类（.dca-sync-mask / .dca-auth-mask）必须保持不透明 background——
曾因透明遮罩导致残留登录页透出，DOM 检查全过但用户看到冻屏（详设 §6）。
"""
from __future__ import annotations

import streamlit as st

_CSS = """
<style>
/* 运行时不压暗页面（去掉白色大蒙版感） */
[data-testid="stElementContainer"][data-stale="true"] { opacity: 1 !important; transition: none !important; }

/* 全局：主页面浅灰（body 兜色）；登录页科技风背景由 .fz-bg 提供，登录后不再渲染即消失 */
body { background: #f7f8fa; }
.stApp { background: transparent; }
[data-testid="stHeader"] { background: transparent !important; }

/* ===== 登录页科技风背景（fixed 全屏，仅登录页渲染） ===== */
.fz-bg { position: fixed; inset: 0; z-index: -1; overflow: hidden; pointer-events: none;
    background:
        radial-gradient(ellipse 90% 55% at 70% -10%, rgba(59,110,255,.28), transparent 60%),
        radial-gradient(ellipse 70% 50% at 15% 110%, rgba(0,198,255,.16), transparent 60%),
        linear-gradient(155deg, #0b1730 0%, #0d1f42 48%, #080f22 100%);
}
.fz-grid { position: absolute; inset: 0;
    background-image: linear-gradient(rgba(126,164,255,.06) 1px, transparent 1px),
                      linear-gradient(90deg, rgba(126,164,255,.06) 1px, transparent 1px);
    background-size: 44px 44px;
    -webkit-mask-image: radial-gradient(ellipse 70% 60% at 50% 42%, black 25%, transparent 78%);
    mask-image: radial-gradient(ellipse 70% 60% at 50% 42%, black 25%, transparent 78%);
}
.fz-stars { position: absolute; left: 0; top: -50%; width: 100%; height: 200%;
    background-image: radial-gradient(rgba(255,255,255,.5) 1px, transparent 1.6px);
    background-size: 140px 140px; opacity: .35;
    animation: fz-drift 46s linear infinite;
}
.fz-stars.s2 { background-size: 220px 220px; opacity: .2; animation-duration: 74s; }
@keyframes fz-drift { to { transform: translateY(-50%); } }
.fz-orb { position: absolute; border-radius: 50%; filter: blur(80px); opacity: .5;
    animation: fz-float 13s ease-in-out infinite; }
.fz-orb.o1 { width: 420px; height: 420px; background: #2456e0; top: -10%; right: -6%; }
.fz-orb.o2 { width: 340px; height: 340px; background: #0096c7; bottom: -12%; left: -5%; animation-delay: -6.5s; }
@keyframes fz-float { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(-24px,28px) scale(1.07); } }

/* 玻璃拟态登录卡 */
[data-testid="stColumn"]:has(.fz-scope) {
    background: rgba(13,24,52,.55);
    -webkit-backdrop-filter: blur(20px); backdrop-filter: blur(20px);
    border: 1px solid rgba(148,180,255,.22); border-radius: 20px;
    box-shadow: 0 30px 80px rgba(2,8,26,.55), inset 0 1px 0 rgba(255,255,255,.08);
    padding: 40px 40px 32px 40px;
    max-width: 420px; margin: 0 auto;
}
.fz-badge { width: 64px; height: 64px; margin: 0 auto; border-radius: 18px;
    background: linear-gradient(135deg, #3d7bff, #2650d8);
    box-shadow: 0 10px 30px rgba(47,110,255,.45), inset 0 1px 0 rgba(255,255,255,.25);
    display: flex; align-items: center; justify-content: center; font-size: 32px; }
.fz-brand { text-align: center; color: rgba(228,238,255,.92); font-size: 16px; font-weight: 600; letter-spacing: 3px; margin-top: 16px; }
.fz-pill { display: inline-block; border: 1px solid rgba(148,180,255,.35); color: rgba(178,198,234,.75);
    border-radius: 999px; padding: 1px 10px; font-size: 11px; letter-spacing: 2px; vertical-align: 2px; margin-left: 8px; font-weight: 400; }
.fz-title { text-align: center; font-size: 24px; font-weight: 600; color: #ffffff; margin: 14px 0 22px 0; letter-spacing: 3px; }
.fz-hint { text-align: center; color: rgba(232,240,255,.92); font-size: 14px; margin-bottom: 12px; line-height: 1.6; }
.fz-hint span { color: rgba(178,198,234,.6); font-size: 12.5px; }
.dca-login-foot { text-align: center; color: rgba(170,190,228,.45); font-size: 12px; margin-top: 12px; }

/* 登录卡内输入框：深色内嵌 + 亮边框（对比加强） */
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] div[data-baseweb="base-input"],
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] [data-testid="stTextInputRootElement"],
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] .react-aria-TextField {
    background: rgba(8,16,38,.62) !important; background-color: rgba(8,16,38,.62) !important;
    border: 1.5px solid rgba(160,190,255,.45) !important; border-radius: 10px !important;
    min-height: 48px; transition: all .15s ease; color-scheme: dark;
}
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] div[data-baseweb="input"]:focus-within,
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] div[data-baseweb="base-input"]:focus-within,
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] [data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] .react-aria-TextField:focus-within {
    background: rgba(13,26,58,.78) !important; background-color: rgba(13,26,58,.78) !important; border-color: #6b9bff !important;
    box-shadow: 0 0 0 3px rgba(91,147,255,.30);
}
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] input { background-color: transparent !important; font-size: 15.5px; font-weight: 500; color: #f2f6ff !important; -webkit-text-fill-color: #f2f6ff; caret-color: #eaf1ff; }
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] input::placeholder { color: rgba(200,216,246,.5); -webkit-text-fill-color: rgba(200,216,246,.5); }
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] input[type="text"],
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] input[type="password"] {
    padding-left: 38px !important;
    background-repeat: no-repeat !important; background-position: 12px center !important; background-size: 16px !important;
}
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] input[type="text"] {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23a9bfe8' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='12' cy='8' r='4'/%3E%3Cpath d='M4 20c0-4 4-6 8-6s8 2 8 6'/%3E%3C/svg%3E") !important;
}
[data-testid="stColumn"]:has(.fz-scope) [data-testid="stTextInput"] input[type="password"] {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23a9bfe8' stroke-width='2' stroke-linecap='round'%3E%3Crect x='5' y='11' width='14' height='9' rx='2'/%3E%3Cpath d='M8 11V8a4 4 0 0 1 8 0v3'/%3E%3C/svg%3E") !important;
}

/* 渐变发光主按钮 */
[data-testid="stColumn"]:has(.fz-scope) button[kind*="FormSubmit"] {
    background: linear-gradient(135deg, #3d7bff 0%, #2f5ff0 55%, #2650d8 100%) !important; color: #ffffff !important; border: none !important;
    border-radius: 10px !important; min-height: 46px; font-size: 15px; font-weight: 500 !important;
    letter-spacing: 4px; text-indent: 4px;
    box-shadow: 0 10px 28px rgba(47,110,255,.38);
    transition: all .18s ease; margin-top: 6px;
}
[data-testid="stColumn"]:has(.fz-scope) button[kind*="FormSubmit"]:hover { filter: brightness(1.1); transform: translateY(-1px); box-shadow: 0 14px 34px rgba(47,110,255,.48); color: #ffffff !important; }
[data-testid="stColumn"]:has(.fz-scope) button[kind*="FormSubmit"]:active { transform: translateY(0); }
[data-testid="stColumn"]:has(.fz-scope) button[kind*="FormSubmit"]:focus { box-shadow: 0 0 0 3px rgba(77,141,255,.35) !important; }

/* 卡内次要按钮（返回登录）：幽灵风 */
[data-testid="stColumn"]:has(.fz-scope) button[kind="secondary"] {
    background: transparent !important; border: 1px solid rgba(255,255,255,.18) !important;
    color: rgba(210,224,250,.75) !important; border-radius: 10px !important;
}

/* 提示条圆角 */
[data-testid="stAlert"] { border-radius: 10px; }

/* 非阻塞浮动加载提示（卡片 + 顶部流光条，pointer-events:none 不拦截点击） */
.dca-loader {
    position: fixed; top: 88px; left: 50%; transform: translateX(-50%);
    z-index: 999999; pointer-events: none;
    display: flex; align-items: center; gap: 14px;
    background: rgba(255,255,255,.97); border: 1px solid #e6eaf2;
    border-radius: 14px; padding: 13px 22px;
    box-shadow: 0 14px 40px rgba(30,41,82,.16);
}
.dca-ring { width: 26px; height: 26px; border-radius: 50%; flex: none;
    border: 3px solid #e3e9f5; border-top-color: #2f6bff;
    animation: dca-spin .8s linear infinite; }
@keyframes dca-spin { to { transform: rotate(360deg); } }
.dca-loader-msg { font-size: 14.5px; font-weight: 600; color: #22315e; white-space: nowrap; }
.dca-loader-sub { font-size: 12px; color: #8490ae; white-space: nowrap; }
.dca-shimmer { position: fixed; top: 60px; left: 0; height: 3px; width: 100%;
    z-index: 999999; pointer-events: none;
    background: linear-gradient(90deg, transparent, rgba(47,107,255,.18), #2f6bff, rgba(47,107,255,.18), transparent);
    background-size: 40% 100%; background-repeat: no-repeat;
    animation: dca-slide 1.15s ease-in-out infinite; }
@keyframes dca-slide { 0% { background-position: -40% 0; } 100% { background-position: 140% 0; } }
/* 登录后整屏同步遮罩：盖住上一个 run 残留的登录页，避免同步期间一直看到登录页 */
.dca-sync-mask { position: fixed; inset: 0; z-index: 999999; background: #f7f8fa;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px; }
.dca-sync-ring { width: 44px; height: 44px; border-radius: 50%; flex: none;
    border: 4px solid #e3e9f5; border-top-color: #2f6bff;
    animation: dca-spin .8s linear infinite; }
.dca-sync-msg { font-size: 16px; font-weight: 600; color: #22315e; letter-spacing: 1px; }
.dca-sync-sub { font-size: 13px; color: #8490ae; }

/* ===== 登录中整屏遮罩：延续登录页科技风，玻璃拟态状态卡 + 分步进度 ===== */
.dca-auth-mask { position: fixed; inset: 0; z-index: 999999;
    display: flex; align-items: center; justify-content: center;
    background:  /* 不透明深色渐变：必须完全盖住上一趟残留的登录页，否则透明遮罩等于没遮 */
        radial-gradient(ellipse 90% 55% at 70% -10%, rgba(59,110,255,.28), transparent 60%),
        radial-gradient(ellipse 70% 50% at 15% 110%, rgba(0,198,255,.16), transparent 60%),
        linear-gradient(155deg, #0b1730 0%, #0d1f42 48%, #080f22 100%); }
.dca-auth-mask .fz-bg { z-index: 0; }  /* 遮罩内科技纹理：盖在遮罩底色上、状态卡之下 */
.dca-auth-mask .dca-auth-card { position: relative; z-index: 1; }
.dca-auth-card { display: flex; flex-direction: column; align-items: center; gap: 15px;
    width: 320px; padding: 36px 32px 30px; border-radius: 20px;
    background: rgba(11,20,44,.78); border: 1px solid rgba(148,180,255,.38);
    -webkit-backdrop-filter: blur(20px); backdrop-filter: blur(20px);
    box-shadow: 0 30px 80px rgba(2,8,26,.55), inset 0 1px 0 rgba(255,255,255,.08);
    animation: dca-auth-in .3s cubic-bezier(.2,.9,.3,1.15); }
@keyframes dca-auth-in { 0% { opacity: 0; transform: translateY(12px) scale(.96); } 100% { opacity: 1; transform: none; } }
.dca-auth-ring { width: 40px; height: 40px; border-radius: 50%;
    border: 3.5px solid rgba(120,160,255,.16); border-top-color: #6b9bff;
    animation: dca-spin .8s linear infinite; }
.dca-auth-title { font-size: 16.5px; font-weight: 600; color: #ffffff; letter-spacing: 3px; }
.dca-auth-steps { display: flex; flex-direction: column; gap: 8px; margin-top: 2px; min-height: 46px; }
.dca-auth-step { display: flex; align-items: center; gap: 9px; font-size: 12.5px;
    color: rgba(178,198,234,.5); letter-spacing: 1px; transition: color .25s ease; }
.dca-auth-step .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.dca-auth-step.on { color: rgba(232,240,255,.95); }
.dca-auth-step.on .dot { background: #6b9bff; box-shadow: 0 0 8px #6b9bff; animation: dca-pulse 1s ease-in-out infinite; }
.dca-auth-step.done { color: rgba(94,214,164,.9); }
.dca-auth-step.done .dot { background: #3ecf8e; }
@keyframes dca-pulse { 0%,100% { opacity: .35; } 50% { opacity: 1; } }
</style>
"""


def inject_css():
    """注入全局样式（每次脚本重跑都要调一次，Streamlit 不保留上一趟的 DOM）。"""
    st.markdown(_CSS, unsafe_allow_html=True)

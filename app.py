# -*- coding: utf-8 -*-
"""动态定投模拟决策台（Streamlit 网页版）

数据与策略完全复用 sp500-nasdaq100-gold-dca skill：
- 行情/决策：子进程调用 scripts/dca_calculator.py（自带缓存、评分模型、月末释放）
- 记账：复述确认后追加 transactions / observations（配置 secrets 后用 Google Sheets 云端存储并按用户隔离；未配置时回退本地 CSV，只追加不覆盖）

启动：streamlit run app.py
多用户：配置 .streamlit/secrets.toml 的 [connections.gsheets] 后自动启用云端存储 + 名字/PIN 门闸
"""

import argparse as _argparse
import contextlib
import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import storage  # 存储层：Google Sheets 优先，本地 CSV 回退
import streamlit as st

# ---- 路径：代码 vs 数据分离 ----
CODE_DIR = Path(__file__).resolve().parent  # 代码目录（scripts/ 在这里）

# 解析 --base-dir（Streamlit 用 -- 分隔自定义参数）
_ap = _argparse.ArgumentParser()
_ap.add_argument("--base-dir", default=None, help="用户独立数据目录（多用户部署时用）")
_args, _ = _ap.parse_known_args()
BASE = Path(_args.base_dir).resolve() if _args.base_dir else CODE_DIR
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


storage.init(DATA_DIR)

TX_CSV = DATA_DIR / "transactions.csv"
OBS_CSV = DATA_DIR / "observations.csv"
try:
    CONFIG = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
except (json.JSONDecodeError, OSError) as _e:
    raise SystemExit(f"缺少或损坏的 data/config.json：{_e}") from None
ASSETS = CONFIG["assets"]
BACKTEST_DIR = (
    Path(_args.base_dir + "/backtest")
    if _args.base_dir and Path(_args.base_dir + "/backtest").exists()
    else CODE_DIR.parent.parent.parent / "backtest-dca-5y"
)

st.set_page_config(page_title="模拟定投决策台", layout="wide", page_icon="📈")


# ---- 全局样式与加载组件 ----
st.markdown(
    """
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
""",
    unsafe_allow_html=True,
)


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



def _render_login_page(names, ph):
    """登录/激活/自举页整体渲染（统一挂在 ph 容器内）。
    提交校验通过时先把 ph 掏空——登录页立刻从 DOM 整体摘除，再挂遮罩并 stash+rerun。
    不再依赖 run 末尾的路径剪枝（实测剪枝不可靠，残留登录元素会一路带进主应用）。"""
    # 科技风背景层（fixed 定位铺全屏；随 ph 容器一起被摘除）
    st.markdown(
        "<div class='fz-bg'><div class='fz-grid'></div><div class='fz-stars'></div>"
        "<div class='fz-stars s2'></div><div class='fz-orb o1'></div><div class='fz-orb o2'></div></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div style='height:12vh'></div>", unsafe_allow_html=True
    )  # 顶部留白（在卡片外，控制整体下移）
    _pad_l, _mid, _pad_r = st.columns([1, 1, 1])
    with _mid:
        st.markdown(
            "<div class='fz-scope'></div>", unsafe_allow_html=True
        )  # 卡片样式标记（CSS 据此限定作用域）
        st.markdown(
            "<div class='fz-badge'>📈</div>"
            "<div class='fz-brand'>模拟定投决策台<span class='fz-pill'>试用版</span></div>"
            "<div class='fz-title'>欢迎登录</div>",
            unsafe_allow_html=True,
        )
        if not names:
            # 自举：系统还没有任何用户时，首个注册者自动成为管理员
            st.markdown(
                "<div class='fz-hint'>首次使用：创建管理员账号</div>",
                unsafe_allow_html=True,
            )
            _berr = st.session_state.pop("_boot_err", None)
            if _berr:
                st.error(_berr)
            with st.form("bootstrap_form"):
                reg_name = st.text_input(
                    "账号",
                    placeholder="请输入管理员名字",
                    label_visibility="collapsed",
                )
                reg_pin = st.text_input(
                    "密码",
                    type="password",
                    placeholder="请设置 PIN（4-8 位，记牢）",
                    label_visibility="collapsed",
                )
                reg_pin2 = st.text_input(
                    "确认密码",
                    type="password",
                    placeholder="请再次输入 PIN",
                    label_visibility="collapsed",
                )
                if st.form_submit_button("创建并进入", use_container_width=True):
                    if not reg_name.strip():
                        st.error("名字不能为空")
                    elif reg_pin != reg_pin2:
                        st.error("两次输入的 PIN 不一致")
                    elif not (4 <= len(reg_pin or "") <= 8):
                        st.error("PIN 需要 4-8 位")
                    else:
                        st.session_state["_auth"] = {
                            "stage": "bootstrap",
                            "name": reg_name.strip(),
                            "pin": reg_pin,
                            "pin2": reg_pin2,
                        }
                        ph.empty()  # 登录页立即整体摘除
                        show_auth_mask(
                            "正在创建管理员", [("创建账号", "on"), ("准备数据", "off")]
                        )
                        st.rerun()
        elif st.session_state.get("activating"):
            who = st.session_state["activating"]
            st.markdown(
                f"<div class='fz-hint'>👋 你好，{who}！首次登录请设置你的 PIN<br><span>只有你自己知道，管理员也看不到</span></div>",
                unsafe_allow_html=True,
            )
            _aerr = st.session_state.pop("_act_err", None)
            if _aerr:
                st.error(_aerr)
            with st.form("activate_form"):
                act_pin = st.text_input(
                    "密码",
                    type="password",
                    placeholder="请设置 PIN（4-8 位，记牢）",
                    label_visibility="collapsed",
                )
                act_pin2 = st.text_input(
                    "确认密码",
                    type="password",
                    placeholder="请再次输入 PIN",
                    label_visibility="collapsed",
                )
                if st.form_submit_button(
                    "设置 PIN 并进入", use_container_width=True
                ):
                    if act_pin != act_pin2:
                        st.error("两次输入的 PIN 不一致")
                    elif not (4 <= len(act_pin or "") <= 8):
                        st.error("PIN 需要 4-8 位")
                    else:
                        st.session_state["_auth"] = {
                            "stage": "activate",
                            "who": who,
                            "pin": act_pin,
                            "pin2": act_pin2,
                        }
                        ph.empty()
                        show_auth_mask(
                            "正在设置 PIN", [("写入云端", "on"), ("同步云端数据", "off")]
                        )
                        st.rerun()
            if st.button("← 返回登录", key="back_login"):
                st.session_state.pop("activating", None)
                st.rerun()
        else:
            _lerr = st.session_state.pop("_login_err", None)
            if _lerr:
                st.error(_lerr)
            with st.form("login_form"):
                login_name = st.text_input(
                    "账号", placeholder="请输入用户名", label_visibility="collapsed"
                )
                login_pin = st.text_input(
                    "密码",
                    type="password",
                    placeholder="请输入密码",
                    label_visibility="collapsed",
                )
                if st.form_submit_button("登 录", use_container_width=True):
                    nm = login_name.strip()
                    if not nm or not login_pin:
                        st.error("请输入账号和密码")
                    else:
                        st.session_state["_auth"] = {
                            "stage": "login",
                            "name": nm,
                            "pin": login_pin,
                        }
                        ph.empty()  # 登录页立即整体摘除
                        show_auth_mask(
                            "正在登录", [("验证账号", "on"), ("同步云端数据", "off")]
                        )
                        st.rerun()
        st.markdown(
            "<div class='dca-login-foot'>忘记 PIN？联系管理员重置 · 每人数据互相隔离</div>",
            unsafe_allow_html=True,
        )

# ---- 用户门闸：Sheets 云端模式必须 名字+PIN 登录；本地 CSV 模式直进 ----
CURRENT_USER = "local"
if storage.sheets_enabled():
    if "user" not in st.session_state:
        _login_ph = st.empty()  # 登录页统一挂载点：每趟运行都在门闸首位创建，保证 delta 路径稳定
        _auth = st.session_state.get("_auth")
        if _auth is not None:
            # —— 第二阶段（点击后的下一趟运行）：先挂整屏「登录中」遮罩，再做一切网络校验/同步。
            # 点击那一趟零网络请求（用户名单走会话缓存），遮罩因此能在点击后立即出现。——
            _stage = _auth.get("stage")
            if _stage == "login":
                _m = show_auth_mask(
                    "正在登录", [("验证账号", "on"), ("同步云端数据", "off")]
                )
                try:
                    _status, _canon, _fresh = storage.authenticate(
                        _auth["name"], _auth["pin"]
                    )
                except Exception:
                    _status, _canon, _fresh = "error", None, None
                if _fresh is not None:
                    st.session_state["_names"] = _fresh  # 顺手刷新会话名单缓存
                st.session_state.pop("_auth", None)
                if _status == "ok" and _canon:
                    st.session_state["user"] = _canon
                    show_auth_mask(
                        "正在登录",
                        [("验证账号", "done"), ("同步云端数据", "on")],
                        ph=_m,
                    )
                    with contextlib.suppress(
                        Exception
                    ):  # 同步失败不阻塞进入，侧栏🔄可重同步
                        storage.sync_local(_canon)
                    st.session_state["synced"] = True
                elif _status == "pending":
                    st.session_state["activating"] = _canon
                elif _status == "no_user":
                    st.session_state["_login_err"] = "账号不存在，请联系管理员开通"
                elif _status == "bad_pin":
                    st.session_state["_login_err"] = "账号或密码不对"
                else:
                    st.session_state["_login_err"] = "网络异常，请稍后重试"
                st.rerun()
            elif _stage == "activate":
                _m = show_auth_mask(
                    "正在设置 PIN", [("写入云端", "on"), ("同步云端数据", "off")]
                )
                try:
                    _ok, _msg = storage.set_pin(
                        _auth["who"], _auth["pin"], _auth["pin2"]
                    )
                except Exception:
                    _ok, _msg = False, "网络异常，请稍后重试"
                st.session_state.pop("_auth", None)
                if _ok:
                    st.session_state.pop("activating", None)
                    st.session_state["user"] = _auth["who"]
                    show_auth_mask(
                        "正在设置 PIN",
                        [("写入云端", "done"), ("同步云端数据", "on")],
                        ph=_m,
                    )
                    with contextlib.suppress(Exception):  # 同步失败不阻塞进入
                        storage.sync_local(_auth["who"])
                    st.session_state["synced"] = True
                else:
                    st.session_state["_act_err"] = (
                        _msg  # activating 保留，回设置 PIN 页报错
                    )
                st.rerun()
            elif _stage == "bootstrap":
                _m = show_auth_mask(
                    "正在创建账号", [("创建管理员账号", "on"), ("同步云端数据", "off")]
                )
                try:
                    _fresh = storage.list_users_fresh()
                    if _fresh:  # 防呆：自举页是会话缓存名单渲染的，可能已过期
                        st.session_state["_names"] = _fresh
                        _ok, _msg = False, "系统已有账号，请直接登录"
                    else:
                        _ok, _msg = storage.create_user(
                            _auth["name"], _auth["pin"], _auth["pin2"], role="admin"
                        )
                except Exception:
                    _ok, _msg = False, "网络异常，请稍后重试"
                st.session_state.pop("_auth", None)
                if _ok:
                    st.session_state["_names"] = [_auth["name"]]
                    st.session_state["user"] = _auth["name"]
                    show_auth_mask(
                        "正在创建账号",
                        [("创建管理员账号", "done"), ("同步云端数据", "on")],
                        ph=_m,
                    )
                    with contextlib.suppress(Exception):  # 同步失败不阻塞进入
                        storage.sync_local(_auth["name"])
                    st.session_state["synced"] = True
                elif _msg == "系统已有账号，请直接登录":
                    st.session_state["_login_err"] = (
                        _msg  # 名单已刷新，下一趟进登录页报错
                    )
                else:
                    st.session_state["_boot_err"] = _msg
                st.rerun()
            else:
                st.session_state.pop("_auth", None)  # 未知阶段：丢弃，回登录页
                st.rerun()
        # —— 登录页渲染：名单走会话缓存，本页运行零网络请求 ——
        names = st.session_state.get("_names")
        if names is None:
            names = storage.list_users()  # 仅每会话首次加载触网一次
            st.session_state["_names"] = names
        # 登录页整体挂进固定容器：提交趟/校验趟里容器保持为空 → 上一趟登录页被整体摘除
        with _login_ph.container():
            _render_login_page(names, _login_ph)
        st.stop()
    CURRENT_USER = st.session_state["user"]
    if not st.session_state.get("synced"):
        _ld = show_sync_mask("正在同步云端数据…", "首次进入稍等几秒")
        storage.sync_local(CURRENT_USER)  # 每会话首次进入同步一次云端数据到本地缓存
        st.session_state["synced"] = True
        _ld.empty()


@st.cache_data(ttl=900, show_spinner=False)  # 加载提示由下方自定义浮动组件负责
def run_model(amount: float | None) -> dict:
    cmd = [
        sys.executable,
        str(CODE_DIR / "scripts" / "dca_calculator.py"),
        "--base-dir",
        str(BASE),
    ]
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


def append_csv(path: Path, fieldnames: list, row: dict) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        w.writerow(row)


def _load_json(path: Path):
    """读取 JSON 文件；损坏/读取失败返回 None，由调用方决定跳过展示。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_xau_spot():
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
            )
            d = json.loads(out.stdout.decode("utf-8")).get("data") or {}
            if d.get("f43"):
                rec = {
                    "price": d["f43"] / 100,
                    "chg_pct": d.get("f170", 0) / 10000,
                    "ts": d.get("f86"),
                }
                with contextlib.suppress(Exception):
                    (DATA_DIR / "xau_spot_last.json").write_text(
                        json.dumps(rec), encoding="utf-8"
                    )
                return rec
        except Exception:
            pass
    try:  # 用最后一次成功值兜底，标记缓存
        last = json.loads((DATA_DIR / "xau_spot_last.json").read_text(encoding="utf-8"))
        last["stale"] = True
        return last
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_btc():
    """比特币实时行情（Yahoo BTC-USD）。失败返回 None。"""
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
        return {
            "price": float(price),
            "chg_pct": float(price) / float(prev) - 1,
            "ts": meta.get("regularMarketTime"),
        }
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def load_price_series():
    """读取缓存行情，用于组合曲线与图表。"""
    scripts_dir = str(CODE_DIR / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import dca_calculator as dca  # type: ignore[import-not-found]  # scripts/ 运行时才加入 sys.path，静态分析解析不到

    cache_dir = DATA_DIR / "market_history"
    series = {}
    for sym in ["SPY", "QQQ", "XAUT-USD"]:
        closes = dca.load_cached_closes(dca.cache_file_for(cache_dir, sym))
        if closes:
            series[sym] = closes
    return series


def portfolio_curve(result: dict):
    """按成交记录重建每日 投入 vs 市值 曲线。"""
    if not TX_CSV.exists() or TX_CSV.stat().st_size == 0:
        return None
    rows = list(csv.DictReader(TX_CSV.open("r", encoding="utf-8-sig")))
    if not rows:
        return None
    series = load_price_series()
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


# ---------------- 侧边栏 ----------------
st.sidebar.title("📈 模拟定投决策台")
if CURRENT_USER != "local":
    _is_admin = storage.is_admin(CURRENT_USER)
    _uc1, _uc2 = st.sidebar.columns([3, 1])
    _uc1.caption(f"👤 {CURRENT_USER}" + (" 👑" if _is_admin else ""))
    if _uc2.button("退出", key="btn_logout"):
        st.session_state.clear()
        st.rerun()
    if _is_admin:
        with st.sidebar.expander("👑 用户管理（管理员）", expanded=False):
            for _u in storage.list_users():
                _r1, _r2, _r3 = st.columns([3, 1, 1])
                _status = "已激活" if storage.is_activated(_u) else "待激活"
                _r1.caption(
                    ("👑 " if storage.is_admin(_u) else "👤 ") + f"{_u}（{_status}）"
                )
                if _u != CURRENT_USER:
                    if _r2.button(
                        "重置",
                        key=f"btn_rst_{_u}",
                        help="清空 PIN，对方下次登录重新自己设置",
                    ):
                        storage.reset_pin(_u)
                        st.rerun()
                    if _r3.button("删除", key=f"btn_del_{_u}"):
                        storage.delete_user(_u)
                        st.rerun()
            st.markdown("---")
            with st.form("admin_add_user"):
                _nn = st.text_input("新用户名字")
                if st.form_submit_button("添加账号", use_container_width=True):
                    _ok, _msg = storage.admin_add_user(_nn)
                    if _ok:
                        st.success(f"已添加：{_nn}（待激活，对方首次登录自己设 PIN）")
                        st.rerun()
                    else:
                        st.error(_msg)

# ---- 先运行模型（后续展示用）----
# amount_in 在下方②区定义；首次运行时取 None（自动）
amount_in = 0.0
_ld = show_loading("正在更新行情并计算今日决策…", "标普500 · 纳指100 · 黄金 · 比特币")
try:
    result = run_model(None)
except Exception as e:
    _ld.empty()
    st.error(f"模型运行失败：{e}")
    st.stop()
_ld.empty()

dec = result["decision"]
ms = result["monthly_budget_status"]
pf = result["portfolio"]

# ---- ① 实时行情（标题后第一位，一目了然）----
QUOTE_ROWS = [
    ("标普500指数", "^GSPC"),
    ("标普ETF · SPY", "SPY"),
    ("纳指100指数", "^NDX"),
    ("纳指ETF · QQQ", "QQQ"),
    ("黄金 XAU 美元（现货）", "XAU_SPOT"),
    ("黄金 XAUT/USDT", "XAUT-USD"),
    ("比特币 BTC/USD", "BTC"),
]


def _quote_html(name, price, chg, stale=False):
    """涨绿跌红（国际惯例）。"""
    if chg is None:
        color, arrow, txt = "#888888", "", "—"
    elif chg >= 0:
        color, arrow, txt = "#16a34a", "▲", f"+{chg * 100:.2f}%"
    else:
        color, arrow, txt = "#dc2626", "▼", f"{chg * 100:.2f}%"
    warn = ' <span style="color:#d97706">⚠️缓存</span>' if stale else ""
    return (
        f'<div style="margin:3px 0"><span style="color:#888888">{name}</span><br>'
        f'<b style="font-size:1.05em">{price:,.2f}</b> '
        f'<span style="color:{color}">{arrow} {txt}</span>{warn}</div>'
    )


def _fail_html(name):
    return f'<div style="margin:3px 0"><span style="color:#888888">{name}</span><br><span style="color:#d97706">获取失败，需复核实时价格</span></div>'


all_ok = all(
    str(mk.get("data_source", "")).startswith(("cache+", "yahoo_chart"))
    for mk in result["markets"].values()
)
st.sidebar.markdown(
    "**📡 实时行情**" + ("（全部正常）" if all_ok else "（⚠️ 部分异常）")
)
quote_times = []
xau = fetch_xau_spot()
btc = fetch_btc()
for name, sym in QUOTE_ROWS:
    if sym == "BTC":
        if btc:
            st.sidebar.markdown(
                _quote_html(name, btc["price"], btc["chg_pct"]), unsafe_allow_html=True
            )
            if btc.get("ts"):
                quote_times.append(btc["ts"])
        else:
            st.sidebar.markdown(_fail_html(name), unsafe_allow_html=True)
        continue
    if sym == "XAU_SPOT":
        if xau:
            st.sidebar.markdown(
                _quote_html(
                    name, xau["price"], xau["chg_pct"], xau.get("stale", False)
                ),
                unsafe_allow_html=True,
            )
            if xau.get("ts"):
                quote_times.append(xau["ts"])
        else:
            mk = result["markets"].get("GC=F", {})
            if mk.get("latest_price") is not None:
                st.sidebar.markdown(
                    _quote_html(
                        "黄金 XAU 美元（期货估算）",
                        mk["latest_price"],
                        mk.get("day_change"),
                        bool(mk.get("cache_warning")),
                    ),
                    unsafe_allow_html=True,
                )
                if mk.get("quote_time"):
                    quote_times.append(mk["quote_time"])
            else:
                st.sidebar.markdown(_fail_html(name), unsafe_allow_html=True)
        continue
    mk = result["markets"].get(sym, {})
    if mk.get("error") or mk.get("latest_price") is None:
        st.sidebar.markdown(_fail_html(name), unsafe_allow_html=True)
        continue
    st.sidebar.markdown(
        _quote_html(
            name,
            mk["latest_price"],
            mk.get("day_change"),
            bool(mk.get("cache_warning")),
        ),
        unsafe_allow_html=True,
    )
    if mk.get("quote_time"):
        quote_times.append(mk["quote_time"])
if quote_times:
    from datetime import datetime as _dt

    st.sidebar.caption(
        f"截至 {_dt.fromtimestamp(max(quote_times)).strftime('%m-%d %H:%M')}"
    )
else:
    latest_dates = {
        mk.get("history_end")
        for mk in result["markets"].values()
        if mk.get("history_end")
    }
    st.sidebar.caption(f"截至 {max(latest_dates) if latest_dates else '?'}")

# ---- ② 基准金额 + 刷新按钮（同一行）----
st.sidebar.markdown("---")
_col_a, _col_r = st.sidebar.columns([3, 1])
with _col_a:
    amount_in = st.number_input(
        "基准金额（0=自动）",
        min_value=0.0,
        value=0.0,
        step=500.0,
        key="amt_base",
        label_visibility="collapsed",
    )
with _col_r:
    if st.button("🔄 刷新", use_container_width=True, key="btn_refresh"):
        st.cache_data.clear()
        st.session_state.pop("synced", None)  # 触发重进时重新从云端同步
        st.rerun()
# 用户输入了非零金额 → 用实际金额重跑模型
if amount_in > 0:
    result = run_model(amount_in)
    dec = result["decision"]
    ms = result["monthly_budget_status"]
    pf = result["portfolio"]

# ---- ③ 汇率 ----
st.sidebar.caption(f"USD/CNY {result['usdcny']} ｜ U/CNY {result.get('usdtcny')}")

# ---- ④ 每月预算（底部，同一行）----
st.sidebar.markdown("---")


def current_budget_display():
    try:
        overrides = storage.get_overrides(CURRENT_USER)
        month = date.today().strftime("%Y-%m")
        keys = [k for k in overrides if isinstance(k, str) and k <= month]
        if keys:
            return float(overrides[max(keys)])
    except Exception:
        pass
    try:
        return float(CONFIG.get("monthly_budget_rmb", 30000))
    except (TypeError, ValueError):
        return 30000.0


_col_b, _col_s = st.sidebar.columns([3, 1])
with _col_b:
    budget_in = st.number_input(
        "💰 每月预算",
        min_value=0.0,
        value=current_budget_display(),
        step=1000.0,
        key="budget_in",
        label_visibility="collapsed",
    )
with _col_s:
    if st.button("💾 保存", use_container_width=True, key="btn_save_budget"):
        try:
            _bval = float(budget_in)
        except (TypeError, ValueError):
            st.error("预算金额无效，未保存")
        else:
            storage.set_override(CURRENT_USER, date.today().strftime("%Y-%m"), _bval)
            st.cache_data.clear()
            st.rerun()

src = ms.get("budget_source", "default")
src_txt = (
    "自定义，自 " + src.split(":")[-1] + " 起生效"
    if src.startswith("override:")
    else "默认值"
)
try:
    _ms_budget = f"¥{float(ms['monthly_budget_rmb']):,.0f}"
except (TypeError, ValueError, KeyError):
    _ms_budget = "¥—"
st.sidebar.caption(f"生效：{_ms_budget}（{src_txt}）")

# ---- 免责声明（始终显示）----
st.sidebar.markdown("---")
st.sidebar.caption(
    "⚠️ 本工具仅为模拟测算与回测工具，不构成任何投资建议。投资有风险，策略无法保证不亏损，请根据自身情况独立决策。"
)

# ---- 本地历史 → 云端一次性迁移（仅 Sheets 模式可见）----
if CURRENT_USER != "local":
    with st.sidebar.expander("📥 本地历史数据迁移"):
        st.caption(
            "把这台电脑 data/ 里的历史记录上传到云端你的名下，只需做一次（上传后本地文件会备份为 .localbak）。"
        )
        if st.button("开始上传", key="btn_import"):
            counts = storage.import_local_to_sheets(CURRENT_USER)
            st.session_state["synced"] = True
            st.success(
                f"已上传：成交 {counts['transactions']} 条｜观察 {counts['observations']} 条｜预算 {counts['budget_overrides']} 项"
            )

# ---------------- 主界面 ----------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "🎯 今日模拟",
        "📊 持仓与曲线",
        "✍️ 记账",
        "📜 历史记录",
        "🧪 回测结果",
        "📖 策略说明",
    ]
)

with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("今日建议", f"¥{dec['suggested_amount_rmb']:,.0f}", dec["level_label"])
    c2.metric(
        "部署系数",
        f"{dec['deploy_multiplier']:.2f}",
        f"基准 ¥{dec['base_amount_rmb']:,.0f}",
    )
    c3.metric(
        "本月可用池",
        f"¥{ms['available_pool_rmb']:,.0f}",
        f"剩余 {ms['remaining_trading_days']} 个交易日",
    )
    c4.metric(
        "每日基准",
        f"¥{ms['daily_reference_rmb']:,.0f}",
        "月末释放" + ("已触发" if ms["month_end_release_active"] else "未触发"),
    )
    st.caption(
        "⚠️ 以上均为模拟测算，不构成投资建议。策略无法保证不亏损，只能通过分批、分散和动态调仓降低永久亏损概率。请根据自身情况独立决策。"
    )

    st.subheader("上一条记录复盘")
    if result["last_records"]:
        st.json(result["last_records"], expanded=False)
        if result.get("since_last_record"):
            cols = st.columns(len(result["since_last_record"]))
            for col, (name, v) in zip(
                cols, result["since_last_record"].items(), strict=False
            ):
                col.metric(
                    f"{name}（自 {v['last_record_date']}）",
                    f"{v['change_pct'] * 100:+.2f}%",
                    f"{v['price_then']:,.1f} → {v['latest_price']:,.1f}",
                )
    else:
        st.info("上一条记录：暂无，本次为第一期测算。")

    st.subheader("累计持仓结果完整表格")
    st.dataframe(
        parse_wide_table(result["wide_table_markdown"]),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("今日行情与评分")
    rows = []
    for key, info in ASSETS.items():
        sym = info.get("index_symbol")
        mk = result["markets"].get(sym, {})
        sc = dec["scores"].get(key, {})
        rows.append(
            {
                "资产": info["name_cn"],
                "最新价": mk.get("latest_price"),
                "日涨跌%": round((mk.get("day_change") or 0) * 100, 2),
                "RSI14": round(mk.get("rsi_14") or 0, 1),
                "距252日高点%": round(
                    (mk.get("drawdown_from_252d_high") or 0) * 100, 1
                ),
                "252日区间位置%": round(
                    (mk.get("position_in_252d_range") or 0) * 100, 1
                ),
                "评分": sc.get("score"),
                "回撤价值": sc.get("value"),
                "趋势": sc.get("trend"),
                "动量": sc.get("momentum"),
                "过热": sc.get("heat"),
                "波动惩罚": sc.get("vol_penalty"),
                "建议比例%": round(result["suggested_weights"].get(key, 0) * 100, 1),
                "建议金额¥": round(
                    dec["suggested_amount_rmb"]
                    * result["suggested_weights"].get(key, 0),
                    2,
                ),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption("决策依据：" + dec["reason"])

with tab2:
    st.subheader("组合市值 vs 累计投入")
    curve = portfolio_curve(result)
    if curve is None:
        st.info("暂无成交记录。记账后这里会显示组合曲线。")
    else:
        st.line_chart(curve)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("累计投入", f"¥{pf['total_invested_rmb']:,.0f}")
        c2.metric("当前市值", f"¥{(pf['current_value_rmb'] or 0):,.0f}")
        c3.metric(
            "未实现盈亏",
            f"¥{(pf['unrealized_pnl_rmb'] or 0):,.0f}",
            f"{(pf['return_rate'] or 0) * 100:+.2f}%",
        )
        xirr_days = pf.get("xirr_period_days")
        c4.metric(
            "年化 XIRR",
            "期短不年化"
            if (xirr_days is not None and xirr_days < 30)
            else (
                f"{(pf.get('xirr') or 0) * 100:.2f}%"
                if pf.get("xirr") is not None
                else "暂无"
            ),
        )

    st.subheader("建议权重 vs 当前持仓权重")
    wrows = []
    pos_by_asset = {p["asset"]: p for p in pf.get("positions", [])}
    for key, info in ASSETS.items():
        cur_w = (pos_by_asset.get(key) or {}).get("portfolio_weight")
        wrows.append(
            {
                "资产": info["name_cn"],
                "建议权重%": round(result["suggested_weights"].get(key, 0) * 100, 1),
                "当前权重%": None if cur_w is None else round(cur_w * 100, 1),
                "中性权重%": round(info["neutral_weight"] * 100, 1),
            }
        )
    st.dataframe(pd.DataFrame(wrows), use_container_width=True, hide_index=True)

    st.subheader("近一年价格走势（缓存收盘）")
    series = load_price_series()
    chart_data = {}
    for sym in ["SPY", "QQQ", "XAUT-USD"]:
        s = series.get(sym, {})
        recent = {
            d: v
            for d, v in s.items()
            if d
            >= str(date.today().year - 1)
            + "-"
            + str(date.today().month).zfill(2)
            + "-01"
        }
        if recent:
            chart_data[sym] = recent
    if chart_data:
        df = pd.DataFrame(chart_data)
        df = df / df.iloc[0] * 100  # 归一化为 100 起点便于同图对比
        st.line_chart(df)
        st.caption("归一化：各资产近一年起点 = 100")

with tab3:
    st.subheader("记录实际成交（买入/卖出）")
    st.caption(
        "流程：填写 → 复述确认 → 写入 transactions.csv（只追加）。建议与成交严格分离。"
    )
    with st.form("tx_form"):
        c1, c2, c3 = st.columns(3)
        tx_date = c1.text_input("日期", value=str(date.today()))
        tx_asset = c2.selectbox(
            "资产", list(ASSETS.keys()), format_func=lambda k: ASSETS[k]["name_cn"]
        )
        tx_action = c3.selectbox("类型", ["buy", "sell"])
        default_sym = ASSETS[tx_asset]["symbol"]
        c4, c5, c6 = st.columns(3)
        tx_symbol = c4.text_input("代码", value=default_sym)
        tx_amount = c5.number_input("金额 RMB", min_value=0.0, step=100.0)
        tx_price = c6.number_input(
            "净值价格 U", min_value=0.0, step=0.01, format="%.4f"
        )
        c7, c8, c9 = st.columns(3)
        default_fx = (
            result.get("usdtcny")
            if ASSETS[tx_asset].get("fx_mode") == "usdt"
            else result["usdcny"]
        )
        try:
            _fx_default = float(default_fx or 6.73)
        except (TypeError, ValueError):
            _fx_default = 6.73
        tx_fx = c7.number_input(
            "汇率（U/CNY 或 USD/CNY）", value=_fx_default, step=0.001, format="%.4f"
        )
        tx_shares = c8.number_input(
            "数量（0 = 按金额÷汇率÷价格自动算）",
            min_value=0.0,
            step=0.001,
            format="%.4f",
        )
        tx_fee = c9.number_input("手续费 RMB", min_value=0.0, value=0.0, step=1.0)
        tx_notes = st.text_input("备注", value="")
        submitted = st.form_submit_button("生成复述")
    if submitted:
        if not tx_amount or not tx_price:
            st.error("金额和净值价格必填；数量可自动计算。")
        else:
            shares = tx_shares if tx_shares else round(tx_amount / tx_fx / tx_price, 6)
            st.session_state["pending_tx"] = {
                "date": tx_date,
                "action": tx_action,
                "asset": tx_asset,
                "symbol": tx_symbol,
                "currency": "USDT",
                "amount_rmb": tx_amount,
                "price": tx_price,
                "shares": shares,
                "fee_rmb": tx_fee,
                "fx_rate": tx_fx,
                "notes": tx_notes,
            }
    if st.session_state.get("pending_tx"):
        p = st.session_state["pending_tx"]
        st.warning(
            f"请确认写入：{p['date']} {p['action']} {ASSETS[p['asset']]['name_cn']}（{p['symbol']}）"
            f"｜金额 ¥{p['amount_rmb']:,.2f}｜净值 {p['price']} U｜数量 {p['shares']}｜汇率 {p['fx_rate']}｜手续费 ¥{p['fee_rmb']}｜{p['notes']}"
        )
        b1, b2 = st.columns(2)
        if b1.button("✅ 确认写入", use_container_width=True):
            storage.append_row("transactions", CURRENT_USER, p)
            st.session_state.pop("pending_tx")
            st.cache_data.clear()
            st.success("已写入成交记录")
            st.rerun()
        if b2.button("❌ 取消", use_container_width=True):
            st.session_state.pop("pending_tx")
            st.rerun()

    st.markdown("---")
    st.subheader("今天不买，记录观察")
    with st.form("obs_form"):
        obs_reason = st.text_input("跳过原因", value=dec["level_label"])
        obs_notes = st.text_input("备注", value="")
        obs_submit = st.form_submit_button("生成复述")
    if obs_submit:
        w = result["suggested_weights"]
        st.session_state["pending_obs"] = {
            "date": str(date.today()),
            "action": "observe",
            "total_suggested_rmb": dec["suggested_amount_rmb"],
            "user_amount_rmb": 0,
            "decision_level": dec["level_label"],
            "sp500_weight": round(w.get("sp500", 0), 4),
            "ndx100_weight": round(w.get("nasdaq100", 0), 4),
            "gold_weight": round(w.get("gold", 0), 4),
            "reason": obs_reason,
            "notes": obs_notes,
        }
    if st.session_state.get("pending_obs"):
        st.warning(
            f"请确认写入观察记录：{json.dumps(st.session_state['pending_obs'], ensure_ascii=False)}"
        )
        if st.button("✅ 确认写入观察", use_container_width=True):
            storage.append_row(
                "observations", CURRENT_USER, st.session_state.pop("pending_obs")
            )
            st.cache_data.clear()
            st.success("已写入观察记录")
            st.rerun()

with tab4:
    st.subheader("成交记录")
    _tx_rows = storage.read_rows("transactions", CURRENT_USER)
    if _tx_rows:
        st.dataframe(pd.DataFrame(_tx_rows), use_container_width=True, hide_index=True)
    else:
        st.info("暂无成交记录。")
    st.subheader("观察记录")
    _obs_rows = storage.read_rows("observations", CURRENT_USER)
    if _obs_rows:
        st.dataframe(pd.DataFrame(_obs_rows), use_container_width=True, hide_index=True)
    else:
        st.info("暂无观察记录。")

with tab5:
    st.subheader("历史回测结果")
    st.caption(
        "以下回测均基于历史数据滚动测算，不代表未来表现。数据窗口、定投频率、金额各标的略有差异，横向仅供参考。"
    )

    # ========== ① 三策略对比（组合级） ==========
    st.markdown("---")
    st.markdown("### 一、三策略对比（组合级，2021-08 → 2026-08，1254 条任意起点路径）")
    cmp_file = BACKTEST_DIR / "results_compare3.json"
    single_file = BACKTEST_DIR / "results_single_compare.json"
    d = _load_json(cmp_file) if cmp_file.exists() else None
    if d is None and cmp_file.exists():
        st.warning("三策略对比结果文件损坏，已跳过展示")
    if d:
        rows = []
        for mode, label in [
            ("dynamic", "A 全动态"),
            ("tilt", "B 定额+动态比例"),
            ("equal", "C 定额等比"),
        ]:
            o = d["overall"][mode]
            rows.append(
                {
                    "策略": label,
                    "收益中位": f"{o['ret_med']:.1%}",
                    "XIRR中位": f"{o['xirr_med']:.2%}",
                    "回撤中位": f"{o['maxdd_med']:.1%}",
                    "最差回撤": f"{o['maxdd_worst']:.1%}",
                    "浮亏天占比": f"{o['uw_ratio_mean']:.1%}",
                    "正收益路径": f"{o['win_rate']:.2%}",
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # 按持有期的分桶对比
        st.markdown("**按持有期分（三策略）**")
        bucket_labels = [
            ("<3mo", "<3个月"),
            ("3-6mo", "3-6个月"),
            ("6-12mo", "6-12个月"),
            ("1-2y", "1-2年"),
            ("2-3y", "2-3年"),
            ("3y+", "3年以上"),
        ]
        b_rows = []
        for bk, bk_cn in bucket_labels:
            for mode, label in [
                ("dynamic", "A 全动态"),
                ("tilt", "B 定额+动态"),
                ("equal", "C 定额等比"),
            ]:
                b = d["buckets"][mode].get(bk, {})
                b_rows.append(
                    {
                        "持有期": bk_cn,
                        "策略": label,
                        "路径数": b.get("n", ""),
                        "胜率": f"{b['win_rate']:.1%}" if "win_rate" in b else "",
                        "收益中位": f"{b['ret_med']:.1%}" if "ret_med" in b else "",
                        "最差收益": f"{b['ret_worst']:.1%}" if "ret_worst" in b else "",
                        "回撤中位": f"{b['maxdd_med']:.1%}" if "maxdd_med" in b else "",
                        "浮亏天占比": f"{b['uw_ratio_med']:.1%}"
                        if "uw_ratio_med" in b
                        else "",
                    }
                )
        st.dataframe(pd.DataFrame(b_rows), use_container_width=True, hide_index=True)

        # 代表性路径
        st.markdown("**代表性起点路径（三策略对比）**")
        ex_rows = []
        for ex in d.get("examples", []):
            for mode, label in [("dynamic", "A"), ("tilt", "B"), ("equal", "C")]:
                e = ex.get(mode, {})
                ex_rows.append(
                    {
                        "起点": ex.get("start", ""),
                        "策略": label,
                        "天数": e.get("days", ""),
                        "累计投入": f"¥{e.get('invested', 0):,.0f}",
                        "期末市值": f"¥{e.get('final_value', 0):,.0f}",
                        "总收益": f"{e.get('simple_return', 0):.1%}",
                        "XIRR": f"{e.get('xirr', 0):.2%}",
                        "最大回撤": f"{e.get('max_nav_dd', 0):.1%}",
                        "浮亏天占比": f"{e.get('uw_ratio', 0):.1%}",
                        "最长连续浮亏": f"{e.get('max_consec_uw', 0)}天",
                    }
                )
        st.dataframe(pd.DataFrame(ex_rows), use_container_width=True, hide_index=True)

    # ========== ② 策略对比说明 ==========
    st.markdown("---")
    st.markdown("""### 二、为什么"定额等比"收益反而最高？

直觉上，动态调仓应该比"傻瓜等比"强。但回测显示 **C 定额等比** 在多数路径上的总收益和回撤都优于 A/B。原因：

1. **黄金占比 33% 的红利**：C 策略固定 1/3 给黄金，而 A/B 的黄金中性权重仅 20%。2021-2026 黄金年化 ~25%，33% 配比天然带来更高收益 + 更低回撤（黄金与美股低相关）。
2. **动态减仓的"牛市代价"**：A 策略在 RSI 高位/趋势过热时会减码（部署系数 <1），但过去 5 年以牛市为主，高位减仓 = 错过涨幅。模型的价值在 **尾部保护**，不在均值增强。
3. **纪律 > 聪明**：三策略 XIRR 中位差距仅 ~0.2pp，说明"坚持投"远比"怎么投"重要。动态机制像保险——你花钱买的是极端行情时少亏，不是平时多赚。

> **结论**：如果你追求简单且最优，定额等比是最"懒"也最强的选择。动态策略适合 **需要纪律约束、怕高位追买** 的投资者，它的核心卖点是 **心理安全垫** 和 **极端行情保护**。
""")

    # ========== ③ 单品种滚动定投回测 ==========
    st.markdown("---")
    st.markdown("### 三、单品种滚动定投回测（每个交易日均可起投）")
    st.caption(
        "以下每个标的的表格，展示不同持有周期下、所有可能起投日的回测统计。"
        "例如「3年 / 中位收益 21%」表示：在过去 ~10 年里，任意交易日开始月投、持有 3 年，中位累计收益为 21%。"
    )

    d2 = _load_json(single_file) if single_file.exists() else None
    if d2 is None and single_file.exists():
        st.warning("单品种回测结果文件损坏，已跳过展示")
    if d2:
        st.markdown("**单品种 + 动态/固定金额对比（1254 条路径，5 年窗口）**")
        s_rows = []
        for key, label in [
            ("sp500", "标普500"),
            ("nasdaq100", "纳指100"),
            ("gold", "黄金"),
        ]:
            v = d2[key]
            for mode, mlabel in [("dynamic", "动态"), ("fixed", "固定")]:
                o = v[mode]
                s_rows.append(
                    {
                        "品种": label,
                        "模式": mlabel,
                        "胜率": f"{o['win_rate']:.1%}",
                        "收益中位": f"{o['ret_med']:.1%}",
                        "最差收益": f"{o.get('ret_worst', 0):.1%}",
                        "XIRR中位": f"{o['xirr_med']:.2%}",
                        "回撤中位": f"{o['maxdd_med']:.1%}",
                        "最差回撤": f"{o['maxdd_worst']:.1%}",
                        "浮亏天占比": f"{o['uw_ratio_mean']:.1%}",
                        "最长连续浮亏": f"{o.get('max_consec_uw_max', '')}天",
                    }
                )
            pw = v["pairwise_dyn_vs_fixed"]
            s_rows.append(
                {
                    "品种": label,
                    "模式": "动态胜率",
                    "胜率": f"{pw['beat_pct']:.1%}",
                    "收益中位": f"平均差 {pw['mean_diff']:+.2%}",
                    "最差收益": "—",
                    "XIRR中位": "—",
                    "回撤中位": "—",
                    "最差回撤": "—",
                    "浮亏天占比": "—",
                    "最长连续浮亏": "—",
                }
            )
        st.dataframe(pd.DataFrame(s_rows), use_container_width=True, hide_index=True)

    # ---- 标普500 滚动表 ----
    st.markdown("#### 标普500（^GSPC，2016-01 → 2026-08，每日定投 ¥100）")
    sp500_rolling = [
        {
            "定投周期": "3个月",
            "样本数": 2598,
            "最好": "+15.6%",
            "中位": "+2.5%",
            "最差": "-27.5%",
            "亏损概率": "23.8%",
            "最差回撤": "-10.9%",
            "XIRR中位": "21.4%",
            "最长修复天数": 13,
        },
        {
            "定投周期": "6个月",
            "样本数": 2537,
            "最好": "+20.0%",
            "中位": "+4.4%",
            "最差": "-27.1%",
            "亏损概率": "19.9%",
            "最差回撤": "-18.5%",
            "XIRR中位": "18.6%",
            "最长修复天数": 26,
        },
        {
            "定投周期": "1年",
            "样本数": 2410,
            "最好": "+21.8%",
            "中位": "+8.3%",
            "最差": "-23.0%",
            "亏损概率": "16.5%",
            "最差回撤": "-26.8%",
            "XIRR中位": "17.0%",
            "最长修复天数": 54,
        },
        {
            "定投周期": "2年",
            "样本数": 2160,
            "最好": "+32.7%",
            "中位": "+15.2%",
            "最差": "-21.7%",
            "亏损概率": "12.9%",
            "最差回撤": "-30.7%",
            "XIRR中位": "14.8%",
            "最长修复天数": 113,
        },
        {
            "定投周期": "3年",
            "样本数": 1910,
            "最好": "+43.8%",
            "中位": "+21.2%",
            "最差": "-18.4%",
            "亏损概率": "2.1%",
            "最差回撤": "-31.9%",
            "XIRR中位": "13.2%",
            "最长修复天数": 127,
        },
        {
            "定投周期": "5年",
            "样本数": 1407,
            "最好": "+60.1%",
            "中位": "+41.3%",
            "最差": "+9.5%",
            "亏损概率": "0%",
            "最差回撤": "-32.6%",
            "XIRR中位": "14.0%",
            "最长修复天数": 300,
        },
        {
            "定投周期": "7年",
            "样本数": 902,
            "最好": "+76.9%",
            "中位": "+56.8%",
            "最差": "+28.7%",
            "亏损概率": "0%",
            "最差回撤": "-32.6%",
            "XIRR中位": "12.8%",
            "最长修复天数": 469,
        },
        {
            "定投周期": "10年",
            "样本数": 149,
            "最好": "+115.0%",
            "中位": "+105.3%",
            "最差": "+83.1%",
            "亏损概率": "0%",
            "最差回撤": "-32.6%",
            "XIRR中位": "13.9%",
            "最长修复天数": 480,
        },
    ]
    st.dataframe(pd.DataFrame(sp500_rolling), use_container_width=True, hide_index=True)
    st.caption(
        "定投 ≥5 年的所有起点均正收益。最差路径回撤 -32.6%（2020 疫情 + 2022 加息），但坚持 5 年后最差仍 +9.5%。"
    )

    # ---- 纳指100 滚动表 ----
    st.markdown("#### 纳指100（QQQ，2016-01 → 2024-12，每月定投 ¥1000）")
    ndx_rolling = [
        {
            "定投周期": "3个月",
            "样本数": 2200,
            "最好": "+21.3%",
            "中位": "+3.4%",
            "最差": "-17.7%",
            "亏损概率": "23.0%",
            "最差回撤": "-28.6%",
            "回撤中位": "-6.6%",
            "XIRR中位": "29.6%",
            "收益率P10": "-3.8%",
            "收益率P90": "+8.3%",
            "最长水下天数": 92,
        },
        {
            "定投周期": "6个月",
            "样本数": 2136,
            "最好": "+29.4%",
            "中位": "+6.4%",
            "最差": "-19.5%",
            "亏损概率": "17.2%",
            "最差回撤": "-28.6%",
            "回撤中位": "-8.8%",
            "XIRR中位": "27.3%",
            "收益率P10": "-5.2%",
            "收益率P90": "+13.0%",
            "最长水下天数": 164,
        },
        {
            "定投周期": "1年",
            "样本数": 2012,
            "最好": "+38.2%",
            "中位": "+13.5%",
            "最差": "-22.3%",
            "亏损概率": "16.4%",
            "最差回撤": "-28.6%",
            "回撤中位": "-10.8%",
            "XIRR中位": "27.7%",
            "收益率P10": "-6.8%",
            "收益率P90": "+22.9%",
            "最长水下天数": 331,
        },
        {
            "定投周期": "2年",
            "样本数": 1762,
            "最好": "+57.1%",
            "中位": "+24.4%",
            "最差": "-20.7%",
            "亏损概率": "14.5%",
            "最差回撤": "-28.6%",
            "回撤中位": "-15.3%",
            "XIRR中位": "22.9%",
            "收益率P10": "-5.6%",
            "收益率P90": "+45.4%",
            "最长水下天数": 465,
        },
        {
            "定投周期": "3年",
            "样本数": 1511,
            "最好": "+76.3%",
            "中位": "+30.8%",
            "最差": "-8.6%",
            "亏损概率": "5.6%",
            "最差回撤": "-28.6%",
            "回撤中位": "-20.4%",
            "XIRR中位": "18.5%",
            "收益率P10": "+5.0%",
            "收益率P90": "+64.1%",
            "最长水下天数": 465,
        },
        {
            "定投周期": "5年",
            "样本数": 1006,
            "最好": "+117.1%",
            "中位": "+57.8%",
            "最差": "+14.6%",
            "亏损概率": "0%",
            "最差回撤": "-29.0%",
            "回撤中位": "-27.2%",
            "XIRR中位": "18.5%",
            "收益率P10": "+28.8%",
            "收益率P90": "+107.0%",
            "最长水下天数": 164,
        },
        {
            "定投周期": "7年",
            "样本数": 503,
            "最好": "+112.7%",
            "中位": "+89.2%",
            "最差": "+45.1%",
            "亏损概率": "0%",
            "最差回撤": "-30.7%",
            "回撤中位": "-29.2%",
            "XIRR中位": "18.0%",
            "收益率P10": "+64.8%",
            "收益率P90": "+102.8%",
            "最长水下天数": 58,
        },
    ]
    st.dataframe(pd.DataFrame(ndx_rolling), use_container_width=True, hide_index=True)
    st.caption(
        "纳指100 是过去 10 年最强资产之一：5 年定投最差仍 +14.6%，7 年最差 +45.1%。但回撤最深（-28.6%~-30.7%），心理承受力要求高。"
    )

    # ---- 黄金 滚动表 ----
    st.markdown("#### 黄金 XAUT/USD（2020-02 → 2026-08，每月定投 $100）")
    gold_rolling = [
        {
            "定投周期": "3个月",
            "样本数": 2280,
            "最好": "+23.0%",
            "中位": "+1.4%",
            "最差": "-12.5%",
            "亏损概率": "35.5%",
            "最差回撤": "-17.9%",
            "回撤中位": "-5.8%",
            "XIRR中位": "11.9%",
            "收益率P10": "-3.6%",
            "收益率P90": "+8.2%",
            "回本最长天数": 52,
            "水下最长天数": 92,
        },
        {
            "定投周期": "6个月",
            "样本数": 2191,
            "最好": "+38.1%",
            "中位": "+3.8%",
            "最差": "-13.1%",
            "亏损概率": "27.7%",
            "最差回撤": "-17.9%",
            "回撤中位": "-6.3%",
            "XIRR中位": "15.7%",
            "收益率P10": "-5.0%",
            "收益率P90": "+13.9%",
            "回本最长天数": 52,
            "水下最长天数": 183,
        },
        {
            "定投周期": "1年",
            "样本数": 2007,
            "最好": "+57.1%",
            "中位": "+6.3%",
            "最差": "-9.8%",
            "亏损概率": "24.5%",
            "最差回撤": "-17.9%",
            "回撤中位": "-7.6%",
            "XIRR中位": "12.9%",
            "收益率P10": "-3.3%",
            "收益率P90": "+24.5%",
            "回本最长天数": 52,
            "水下最长天数": 294,
        },
        {
            "定投周期": "2年",
            "样本数": 1642,
            "最好": "+92.7%",
            "中位": "+20.2%",
            "最差": "-10.2%",
            "亏损概率": "12.4%",
            "最差回撤": "-18.3%",
            "回撤中位": "-8.0%",
            "XIRR中位": "19.5%",
            "收益率P10": "-1.3%",
            "收益率P90": "+51.9%",
            "回本最长天数": 12,
            "水下最长天数": 294,
        },
        {
            "定投周期": "3年",
            "样本数": 1276,
            "最好": "+122.0%",
            "中位": "+32.8%",
            "最差": "-1.0%",
            "亏损概率": "0.4%",
            "最差回撤": "-22.4%",
            "回撤中位": "-8.0%",
            "XIRR中位": "19.7%",
            "收益率P10": "+5.5%",
            "收益率P90": "+77.7%",
            "回本最长天数": 4,
            "水下最长天数": 294,
        },
        {
            "定投周期": "5年",
            "样本数": 546,
            "最好": "+156.0%",
            "中位": "+80.5%",
            "最差": "+46.6%",
            "亏损概率": "0%",
            "最差回撤": "-25.0%",
            "回撤中位": "-9.9%",
            "XIRR中位": "24.2%",
            "收益率P10": "+56.8%",
            "收益率P90": "+119.6%",
            "回本最长天数": 4,
            "水下最长天数": 199,
        },
    ]
    st.dataframe(pd.DataFrame(gold_rolling), use_container_width=True, hide_index=True)
    st.caption(
        "黄金 2020 年以来表现极强：3 年定投仅 0.4% 概率亏损，5 年全部正收益且最差仍 +46.6%。但注意 XAUT 历史仅 ~6 年，7 年/10 年无数据。"
    )

    # ---- 沪深300 滚动表 ----
    st.markdown("#### 沪深300（000300.SS，2016-01 → 2026-08，每月定投 ¥1000）")
    st.caption(
        "⚠️ 沪深300 不在当前策略标的中，但作为 A 股代表，其回测结果有重要参考价值。"
    )
    hs300_rolling = [
        {
            "定投周期": "3个月",
            "样本数": 2507,
            "最好": "+21.4%",
            "中位": "+0.5%",
            "最差": "-12.6%",
            "亏损概率": "45.0%",
            "最差回撤": "-19.4%",
            "XIRR中位": "4.4%",
            "回本最长天数": 101,
        },
        {
            "定投周期": "6个月",
            "样本数": 2454,
            "最好": "+21.1%",
            "中位": "+1.3%",
            "最差": "-16.4%",
            "亏损概率": "40.7%",
            "最差回撤": "-19.4%",
            "XIRR中位": "5.5%",
            "回本最长天数": 192,
        },
        {
            "定投周期": "1年",
            "样本数": 2330,
            "最好": "+28.4%",
            "中位": "+3.5%",
            "最差": "-20.3%",
            "亏损概率": "40.6%",
            "最差回撤": "-19.4%",
            "XIRR中位": "7.1%",
            "回本最长天数": 370,
        },
        {
            "定投周期": "2年",
            "样本数": 2088,
            "最好": "+39.4%",
            "中位": "+5.2%",
            "最差": "-24.1%",
            "亏损概率": "40.1%",
            "最差回撤": "-19.4%",
            "XIRR中位": "5.2%",
            "回本最长天数": 670,
        },
        {
            "定投周期": "3年",
            "样本数": 1846,
            "最好": "+48.0%",
            "中位": "+5.1%",
            "最差": "-24.7%",
            "亏损概率": "36.9%",
            "最差回撤": "-19.4%",
            "XIRR中位": "3.4%",
            "回本最长天数": 1018,
        },
        {
            "定投周期": "5年",
            "样本数": 1362,
            "最好": "+55.4%",
            "中位": "-0.6%",
            "最差": "-23.2%",
            "亏损概率": "52.0%",
            "最差回撤": "-20.9%",
            "XIRR中位": "-0.2%",
            "回本最长天数": 1224,
        },
        {
            "定投周期": "7年",
            "样本数": 875,
            "最好": "+22.4%",
            "中位": "-1.0%",
            "最差": "-20.7%",
            "亏损概率": "54.4%",
            "最差回撤": "-23.9%",
            "XIRR中位": "-0.3%",
            "回本最长天数": 826,
        },
        {
            "定投周期": "10年",
            "样本数": 146,
            "最好": "+28.4%",
            "中位": "+21.6%",
            "最差": "+13.3%",
            "亏损概率": "0%",
            "最差回撤": "-23.9%",
            "XIRR中位": "3.9%",
            "回本最长天数": 418,
        },
    ]
    st.dataframe(pd.DataFrame(hs300_rolling), use_container_width=True, hide_index=True)
    st.caption(
        "沪深300 是四标的中表现最弱的：5 年定投亏损概率高达 52%，中位收益 -0.6%。但 10 年定投全部正收益（+13.3%~+28.4%），说明 A 股需要更长持有期。"
    )

    # ========== ④ 四标的横向对比 ==========
    st.markdown("---")
    st.markdown("### 四、四标的横向对比（关键指标速览）")
    cross_rows = [
        {
            "标的": "标普500",
            "数据区间": "2016-01~2026-08",
            "3年中位收益": "+21.2%",
            "5年中位收益": "+41.3%",
            "5年亏损概率": "0%",
            "5年最差": "+9.5%",
            "最差回撤": "-32.6%",
            "XIRR中位(5y)": "14.0%",
        },
        {
            "标的": "纳指100",
            "数据区间": "2016-01~2024-12",
            "3年中位收益": "+30.8%",
            "5年中位收益": "+57.8%",
            "5年亏损概率": "0%",
            "5年最差": "+14.6%",
            "最差回撤": "-30.7%",
            "XIRR中位(5y)": "18.5%",
        },
        {
            "标的": "黄金XAUT",
            "数据区间": "2020-02~2026-08",
            "3年中位收益": "+32.8%",
            "5年中位收益": "+80.5%",
            "5年亏损概率": "0%",
            "5年最差": "+46.6%",
            "最差回撤": "-25.0%",
            "XIRR中位(5y)": "24.2%",
        },
        {
            "标的": "沪深300",
            "数据区间": "2016-01~2026-08",
            "3年中位收益": "+5.1%",
            "5年中位收益": "-0.6%",
            "5年亏损概率": "52%",
            "5年最差": "-23.2%",
            "最差回撤": "-23.9%",
            "XIRR中位(5y)": "-0.2%",
        },
    ]
    st.dataframe(pd.DataFrame(cross_rows), use_container_width=True, hide_index=True)

    # ========== ⑤ 综合结论 ==========
    st.markdown("---")
    st.markdown("""### 五、综合结论与说明

**1. 坚持定投是最强的"策略"**
- 标普/纳指/黄金：5 年定投全部正收益，无论起点。
- 即使最差起点（2020 疫情 / 2022 加息顶），坚持 5 年后最差仍有 +9.5%~+46.6%。
- 沪深300 是唯一例外：5 年亏损概率 52%，但拉长到 10 年则 100% 正收益。

**2. 回撤不可避免，但可以承受**
- 标普/纳指最差回撤 ~-30%，黄金 ~-25%，沪深300 ~-24%。
- 三资产组合（35/45/20 权重）回撤中位仅 -13%~-16%，分散本身就是最大的风控。

**3. 动态策略 vs 定额等比**
- 定额等比胜出的核心原因是黄金固定 33%（vs 动态的 20%），而非"等比"本身。
- 动态策略的真正价值在于：**高位减码防爆** 和 **纪律约束**——它更像一份保险，平时付出少量收益"保费"，换取极端行情时的保护。
- XIRR 差距仅 ~0.2pp，说明两种方式的资金效率几乎相同。

**4. 窗口效应声明**
- 2021-2026 窗口以美股牛市 + 黄金大牛市为主，回测结果偏乐观。
- 终点在高位会导致"所有路径都赚钱"的错觉——未来如果进入长期熊市，结果会显著不同。
- 沪深300 的弱势表现是一个有价值的"对照组"：它提醒我们，不是所有市场都"定投就赚"。

**5. 不同投资者的建议**
- **极简派**：定额等比（1/3 标普 + 1/3 纳指 + 1/3 黄金），最懒也最强。
- **纪律派**：用动态策略的每日建议，克服追涨杀跌的人性弱点。
- **保守派**：提高黄金权重到 30-40%，牺牲少量收益换取更低回撤。
""")

    st.caption("完整回测数据文件见 backtest-dca-5y/ 及各标的独立回测目录。")

with tab6:
    st.markdown("""
# 📖 策略说明

## 这是什么

一套**标普500 / 纳指100 / 黄金（XAUT）三资产动态定投系统**：本地 Python 决策引擎 + 本网页面板。
它不预测市场涨跌，而是每天用最新行情计算"今天这笔钱的性价比"，回答三个问题：**今天买不买、买多少、怎么分**。

## 能帮你做什么

- **每日决策**：给出建议总金额、三资产金额与比例、保守/平衡/进取三档执行方案
- **仓位管理**：自动汇总持仓、估值、浮盈浮亏、年化 XIRR（按每笔资金实际进出日期计算）
- **预算纪律**：每月 30,000 预算按交易日摊平，防止"月初冲动、月末干瞪眼"
- **风险保护**：高位过热自动减码、趋势破坏时拒绝接飞刀、黄金在权益走弱时自动升权
- **完整记账**：成交/跳过都有记录，每天先复盘上期结果再出新建议

## 资产与中性权重

| 资产 | 权重 | 区间 | 角色 |
|---|---:|---:|---|
| 标普500（SPY） | 35% | 20%~55% | 核心权益底仓 |
| 纳指100（QQQ） | 45% | 30%~70% | 收益弹性主力 |
| 黄金（XAUT） | 20% | 10%~30% | 防御、抗通胀、对冲 |

## 每天的决策流程（闭环）

```
复盘上一条记录 → 增量更新行情（本地缓存，只抓新增）→ 算本月可用池
→ 连续评分（每资产）→ 定金额（部署系数）→ 定比例（评分倾斜）
→ 输出建议 → 你实盘执行 → 回报成交 → 复述确认后才记账 → 次日再循环
```

## 评分模型（每天对每个资产计算，系数每天不同）

`score = 0.50×回撤价值 + 0.25×趋势 + 0.15×动量 − 0.45×过热 − 0.20×过热² − 0.15×波动`

- **回撤价值**：距一年高点越深越值得买，但乘以"趋势健康门控"——趋势破坏时回撤是飞刀不是机会
- **趋势/动量**：只封顶防御（+0.4/+0.5 封顶），不因强势而追买
- **过热**：RSI + 区间位置 + 短期涨幅合成，带二次项，极端行情能压到不买
- **波动**：年化波动超 18% 的部分开始惩罚

## 金额怎么定

```
作废份额 = 30000 × 启动前已过去交易日 ÷ 当月总交易日（月中启动不追补）
可用池   = 30000 − 本月已投 − 作废份额
每日基准 = 可用池 ÷ 剩余交易日（约 1500）
今日金额 = 每日基准 × 部署系数（clip(1 + 1.1×权益综合评分, 0, 1.8)）
```

- **跳过再平均**：策略不买或你主动跳过的日子，金额不作废，自动摊入后续每个交易日
- **月末释放**：剩余 ≤7 天且可用池 > 0 时，当日下限抬到当日基准（但不推翻"不买"）

## 比例怎么定

`比例 = 中性权重 × (1 + 0.9×资产评分)`，黄金在权益趋势走弱时额外获得防御加成，最后截断到各资产区间并归一化。

## 纪律与边界

- **建议 ≠ 成交**：脚本从不自动记账，只有你确认复述内容后才追加写入（只追加、不覆盖）
- **无法保证不亏**：策略接受浮亏换长期收益，只能通过分散和调仓降低永久亏损概率
- **不碰杠杆**、不擅自加新标的；黄金估值用 XAUT-USD ± 1% 偏差，实际盈亏以你报的欧易成交净值为准

## 回测结论（2021-08 ~ 2026-08，1254 条任意起点路径，诚实版）

- 坚持定投超 3 个月的路径全部正收益（注意：终点在高位，有窗口效应）
- 最痛路径：-28.4% 回撤、连续 248 个交易日浮亏，最终 +67.6%
- 动态机制 vs 固定金额：牛市里代价约 0.3~0.9pp XIRR（"保费"），冲顶回落行情里明显胜出（黄金 +3.7pp）——**它是尾部保险，不是收益增强器**
- 任何单品种回撤都远深于三资产组合：分散本身就是最大的风控

## 数据与隐私

行情来自 Yahoo Finance / 东财公开接口；所有记录（transactions.csv / observations.csv）和行情缓存都在本地 skill 目录，不上传任何地方。
""")

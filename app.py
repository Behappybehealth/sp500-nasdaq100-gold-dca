# -*- coding: utf-8 -*-
"""动态定投模拟决策台（Streamlit 网页版）

数据与策略完全复用 sp500-nasdaq100-gold-dca skill：
- 行情/决策：子进程调用 scripts/dca_calculator.py（自带缓存、评分模型、月末释放）
- 记账：复述确认后追加 transactions / observations（配置 secrets 后用 Google Sheets 云端存储并按用户隔离；未配置时回退本地 CSV，只追加不覆盖）

启动：streamlit run app.py
多用户：配置 .streamlit/secrets.toml 的 [connections.gsheets] 后自动启用云端存储 + 名字/PIN 门闸
"""

import contextlib
import json
import os
from datetime import date

import pandas as pd
import storage  # 存储层：Google Sheets 优先，本地 CSV 回退
import streamlit as st

from src.context import build_paths
from src.services.curves import _load_json, load_price_series, portfolio_curve
from src.services.model import parse_wide_table, run_model
from src.services.quotes import fetch_btc, fetch_xau_spot
from src.ui.overlays import show_auth_mask, show_loading, show_sync_mask
from src.ui.styles import inject_css

# ---- 路径：代码 vs 数据分离（启动逻辑已收编 src/context.py，BUG-020 刀 2）----
_paths = build_paths()
# 过渡桥：未搬走的段（认证/侧栏/tabs）仍引用模块级全局，随后续刀次逐刀收敛
CODE_DIR = _paths.code_dir
BASE = _paths.base
DATA_DIR = _paths.data_dir
TX_CSV = _paths.tx_csv
CONFIG = _paths.config
ASSETS = _paths.assets
BACKTEST_DIR = _paths.backtest_dir

storage.init(DATA_DIR)

st.set_page_config(page_title="模拟定投决策台", layout="wide", page_icon="📈")


# ---- 全局样式与加载组件 ----
# CSS 已搬至 src/ui/styles.py（BUG-020 刀 3）；遮罩的不透明 background 是冻屏坑防线，详见该文件头注。
inject_css()

# ---- 遮罩组件已搬至 src/ui/overlays.py（BUG-020 刀 3）：
# show_loading / show_sync_mask / show_auth_mask，调用点不变（函数名同名 import）。

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
                    placeholder="请设置 PIN（6-8 位，记牢）",
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
                        st.error("PIN 需要 6-8 位")
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
                    placeholder="请设置 PIN（6-8 位，记牢）",
                    label_visibility="collapsed",
                )
                act_pin2 = st.text_input(
                    "确认密码",
                    type="password",
                    placeholder="请再次输入 PIN",
                    label_visibility="collapsed",
                )
                if st.form_submit_button("设置 PIN 并进入", use_container_width=True):
                    if act_pin != act_pin2:
                        st.error("两次输入的 PIN 不一致")
                    elif not (4 <= len(act_pin or "") <= 8):
                        st.error("PIN 需要 6-8 位")
                    else:
                        st.session_state["_auth"] = {
                            "stage": "activate",
                            "who": who,
                            "pin": act_pin,
                            "pin2": act_pin2,
                        }
                        ph.empty()
                        show_auth_mask(
                            "正在设置 PIN",
                            [("写入云端", "on"), ("同步云端数据", "off")],
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


# ---- 认证模式（BUG-003 fail-closed）：默认 sheets；只有显式 DCA_AUTH_MODE=local 才进单机模式 ----
# 原则：安全策略必须是显式声明的，「读不到凭据」永远不等于「不需要登录」。
AUTH_MODE = os.environ.get("DCA_AUTH_MODE", "sheets").strip().lower()
CURRENT_USER = "local"
if AUTH_MODE == "local":
    st.warning("⚠️ 单机模式（DCA_AUTH_MODE=local）：未启用登录，数据仅存在本机 CSV。", icon="⚠️")
elif storage.sheets_status() != "ok":
    if storage.sheets_status() == "error":
        st.error(
            "⛔ 配置损坏：读取 secrets 时出错。为安全起见已停止（fail-closed）。"
            "请检查 .streamlit/secrets.toml 或 Cloud 后台的 secrets 配置。"
        )
    else:
        st.error(
            "⛔ 配置缺失：当前要求云端模式（默认 AUTH_MODE=sheets）但未配置 Google 凭据。"
            "请配置 secrets，或显式设置环境变量 DCA_AUTH_MODE=local 进入单机模式。"
        )
    st.stop()
else:  # sheets 就绪 → 名字+PIN 门闸
    if "user" not in st.session_state:
        _login_ph = (
            st.empty()
        )  # 登录页统一挂载点：每趟运行都在门闸首位创建，保证 delta 路径稳定
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
                elif _status == "locked":
                    st.session_state["_login_err"] = (
                        "失败次数过多，账号已锁定，请 15 分钟后重试"
                    )
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
            try:
                names = storage.list_users()  # 仅每会话首次加载触网一次
            except Exception:
                # BUG-002：读不出名单绝不能渲染登录/自举表单——否则访客看到的就是"创建管理员"
                st.error("☁️ 云端存储暂时不可用，请稍后刷新重试。")
                st.stop()
            st.session_state["_names"] = names
        # 登录页整体挂进固定容器：提交趟/校验趟里容器保持为空 → 上一趟登录页被整体摘除
        with _login_ph.container():
            _render_login_page(names, _login_ph)
        st.stop()
    CURRENT_USER = st.session_state["user"]
    if not st.session_state.get("synced"):
        _ld = show_sync_mask("正在同步云端数据…", "首次进入稍等几秒")
        try:
            storage.sync_local(CURRENT_USER)  # 每会话首次进入同步一次云端数据到本地缓存
        except Exception:
            # 同步失败不阻塞进入（侧栏🔄可重同步），但必须可见——否则建议会基于陈旧缓存静默出错
            st.warning("⚠️ 云端同步失败，本次建议基于本地缓存，可能不是最新。请稍后点侧栏 🔄 重试。")
        st.session_state["synced"] = True
        _ld.empty()


# ---- 服务函数已搬至 src/services/（BUG-020 刀 2）：
# run_model / parse_wide_table → services/model.py；fetch_xau_spot / fetch_btc → services/quotes.py；
# _load_json / load_price_series / portfolio_curve → services/curves.py。调用点显式传 _paths。

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
                        try:
                            storage.reset_pin(_u)
                        except Exception as _e:
                            st.error(f"重置失败：云端存储暂时不可用（{_e}），未做任何改动。")
                        else:
                            st.rerun()
                    if _r3.button("删除", key=f"btn_del_{_u}"):
                        try:
                            storage.delete_user(_u)
                        except Exception as _e:
                            st.error(f"删除失败：云端存储暂时不可用（{_e}），未做任何改动。")
                        else:
                            st.rerun()
            st.markdown("---")
            with st.form("admin_add_user"):
                _nn = st.text_input("新用户名字")
                if st.form_submit_button("添加账号", use_container_width=True):
                    try:
                        _ok, _msg = storage.admin_add_user(_nn)
                    except Exception as _e:
                        st.error(f"添加失败：云端存储暂时不可用（{_e}），未做任何改动。")
                    else:
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
    result = run_model(None, CURRENT_USER, _paths)
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
xau = fetch_xau_spot(_paths)
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
    result = run_model(amount_in, CURRENT_USER, _paths)
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
            try:
                storage.set_override(CURRENT_USER, date.today().strftime("%Y-%m"), _bval)
            except Exception as _e:
                st.error(f"预算保存失败：云端存储暂时不可用（{_e}）。数据未被覆盖，请稍后重试。")
            else:
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
    curve = portfolio_curve(result, _paths)
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
    series = load_price_series(_paths)
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
            try:
                storage.append_row("transactions", CURRENT_USER, p)
            except Exception as _e:
                st.error(f"写入失败：云端存储暂时不可用（{_e}）。历史数据未被覆盖，请稍后重试。")
            else:
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
            try:
                storage.append_row(
                    "observations", CURRENT_USER, st.session_state.pop("pending_obs")
                )
            except Exception as _e:
                st.error(f"写入失败：云端存储暂时不可用（{_e}）。历史数据未被覆盖，请稍后重试。")
            else:
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
    if d is None:
        st.warning(f"三策略对比结果不可用（缺失或损坏）：{cmp_file}")
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
    if d2 is None:
        st.warning(f"单品种回测结果不可用（缺失或损坏）：{single_file}")
    rolling_file = BACKTEST_DIR / "results_rolling.json"
    d3 = _load_json(rolling_file) if rolling_file.exists() else None
    if d3 is None:
        st.warning(f"滚动定投回测结果不可用（缺失或损坏）：{rolling_file}")
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
    if d3:
        st.dataframe(pd.DataFrame(d3["sp500"]), use_container_width=True, hide_index=True)
    st.caption(
        "定投 ≥5 年的所有起点均正收益。最差路径回撤 -32.6%（2020 疫情 + 2022 加息），但坚持 5 年后最差仍 +9.5%。"
    )

    # ---- 纳指100 滚动表 ----
    st.markdown("#### 纳指100（QQQ，2016-01 → 2024-12，每月定投 ¥1000）")
    if d3:
        st.dataframe(pd.DataFrame(d3["nasdaq100"]), use_container_width=True, hide_index=True)
    st.caption(
        "纳指100 是过去 10 年最强资产之一：5 年定投最差仍 +14.6%，7 年最差 +45.1%。但回撤最深（-28.6%~-30.7%），心理承受力要求高。"
    )

    # ---- 黄金 滚动表 ----
    st.markdown("#### 黄金 XAUT/USD（2020-02 → 2026-08，每月定投 $100）")
    if d3:
        st.dataframe(pd.DataFrame(d3["gold"]), use_container_width=True, hide_index=True)
    st.caption(
        "黄金 2020 年以来表现极强：3 年定投仅 0.4% 概率亏损，5 年全部正收益且最差仍 +46.6%。但注意 XAUT 历史仅 ~6 年，7 年/10 年无数据。"
    )

    # ---- 沪深300 滚动表 ----
    st.markdown("#### 沪深300（000300.SS，2016-01 → 2026-08，每月定投 ¥1000）")
    st.caption(
        "⚠️ 沪深300 不在当前策略标的中，但作为 A 股代表，其回测结果有重要参考价值。"
    )
    if d3:
        st.dataframe(pd.DataFrame(d3["hs300"]), use_container_width=True, hide_index=True)
    st.caption(
        "沪深300 是四标的中表现最弱的：5 年定投亏损概率高达 52%，中位收益 -0.6%。但 10 年定投全部正收益（+13.3%~+28.4%），说明 A 股需要更长持有期。"
    )

    # ========== ④ 四标的横向对比 ==========
    st.markdown("---")
    st.markdown("### 四、四标的横向对比（关键指标速览）")
    if d3:
        st.dataframe(pd.DataFrame(d3["cross"]), use_container_width=True, hide_index=True)

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

    st.caption("完整回测数据文件见 backtest/ 目录（results_compare3 / results_single_compare / results_rolling 三个 JSON）。")

with tab6:
    # 策略说明唯一事实源是 strategy/core-strategy.md（BUG-026：删掉内嵌副本，杜绝双份漂移）
    try:
        st.markdown(
            (CODE_DIR / "strategy" / "core-strategy.md").read_text(encoding="utf-8")
        )
    except OSError as _e:
        st.error(f"策略说明文件读取失败：{_e}")

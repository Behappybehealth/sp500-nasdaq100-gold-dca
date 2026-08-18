# -*- coding: utf-8 -*-
"""侧边栏：用户管理 + 实时行情 + 基准金额 + 汇率 + 预算 + 免责声明 + 本地历史迁移，并在这里跑模型。

BUG-020 刀 6 从 app.py:357-634 原样搬入；仅三处结构性调整（零逻辑改动）：
1. 全局 CURRENT_USER / _paths / CONFIG 换成显式参数（render(paths, user)，
   current_budget_display(user, config)）
2. QUOTE_ROWS / _quote_html / _fail_html / current_budget_display 四个纯辅助
   提升到模块级（在原文件里它们定义于侧栏段落中间，不闭包任何局部变量）
3. result/dec/ms/pf 不再落模块作用域，收口为返回值 Decision（src/context.py）

⚠️ 本模块是「模型会跑两次」病灶（BUG-024）的唯一执行点：首次自动定额 run_model(None)，
用户手填金额后整体重跑 run_model(amount_in)。修 BUG-024 改这里。
"""
from __future__ import annotations

from datetime import date

import storage
import streamlit as st

from ..context import Decision, Paths
from ..services.model import run_model
from ..services.quotes import fetch_btc, fetch_xau_spot
from .overlays import show_loading

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


def current_budget_display(user, config):
    try:
        overrides = storage.get_overrides(user)
        month = date.today().strftime("%Y-%m")
        keys = [k for k in overrides if isinstance(k, str) and k <= month]
        if keys:
            return float(overrides[max(keys)])
    except Exception:
        pass
    try:
        return float(config.get("monthly_budget_rmb", 30000))
    except (TypeError, ValueError):
        return 30000.0


def render(paths: Paths, user: str) -> Decision:
    """渲染侧边栏并跑模型，返回 Decision（result/dec/ms/pf）。"""
    st.sidebar.title("📈 模拟定投决策台")
    if user != "local":
        _is_admin = storage.is_admin(user)
        _uc1, _uc2 = st.sidebar.columns([3, 1])
        _uc1.caption(f"👤 {user}" + (" 👑" if _is_admin else ""))
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
                    if _u != user:
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
        result = run_model(None, user, paths)
    except Exception as e:
        _ld.empty()
        st.error(f"模型运行失败：{e}")
        st.stop()
    _ld.empty()

    dec = result["decision"]
    ms = result["monthly_budget_status"]
    pf = result["portfolio"]

    all_ok = all(
        str(mk.get("data_source", "")).startswith(("cache+", "yahoo_chart"))
        for mk in result["markets"].values()
    )
    st.sidebar.markdown(
        "**📡 实时行情**" + ("（全部正常）" if all_ok else "（⚠️ 部分异常）")
    )
    quote_times = []
    xau = fetch_xau_spot(paths)
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
        result = run_model(amount_in, user, paths)
        dec = result["decision"]
        ms = result["monthly_budget_status"]
        pf = result["portfolio"]

    # ---- ③ 汇率 ----
    st.sidebar.caption(f"USD/CNY {result['usdcny']} ｜ U/CNY {result.get('usdtcny')}")

    # ---- ④ 每月预算（底部，同一行）----
    st.sidebar.markdown("---")

    _col_b, _col_s = st.sidebar.columns([3, 1])
    with _col_b:
        budget_in = st.number_input(
            "💰 每月预算",
            min_value=0.0,
            value=current_budget_display(user, paths.config),
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
                    storage.set_override(user, date.today().strftime("%Y-%m"), _bval)
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
    if user != "local":
        with st.sidebar.expander("📥 本地历史数据迁移"):
            st.caption(
                "把这台电脑 data/ 里的历史记录上传到云端你的名下，只需做一次（上传后本地文件会备份为 .localbak）。"
            )
            if st.button("开始上传", key="btn_import"):
                counts = storage.import_local_to_sheets(user)
                st.session_state["synced"] = True
                st.success(
                    f"已上传：成交 {counts['transactions']} 条｜观察 {counts['observations']} 条｜预算 {counts['budget_overrides']} 项"
                )

    return Decision(result=result, dec=dec, ms=ms, pf=pf)

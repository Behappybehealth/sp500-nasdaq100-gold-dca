# -*- coding: utf-8 -*-
"""认证门闸：名字+PIN 三阶段状态机（login / activate / bootstrap）+ 会话首同步。

require_user() 无需参数：全部输入走 storage / session_state / 环境变量，通过返回值交出用户名。

⚠️ 两段式防残留设计（点击趟零网络 I/O → ph.empty() 真删除 → 遮罩 → rerun；
遮罩必须写不透明 background），详见 docs/ARCHITECTURE-DETAIL.md §6 —— 不要轻易改动。
"""
from __future__ import annotations

import logging
import os

import storage
import streamlit as st

from ..state import (
    K_ACTIVATING,
    K_ACT_ERR,
    K_AUTH,
    K_BOOT_ERR,
    K_LOGIN_ERR,
    K_NAMES,
    K_SYNCED,
    K_USER,
)
from .overlays import show_auth_mask, show_sync_mask

# 日志频道（配置在 src/obs.py）。认证只记账号名与结果码，**绝不记 PIN**。
_log = logging.getLogger("dca.auth")


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
            _berr = st.session_state.pop(K_BOOT_ERR, None)
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
                if st.form_submit_button("创建并进入", width="stretch"):
                    if not reg_name.strip():
                        st.error("名字不能为空")
                    elif reg_pin != reg_pin2:
                        st.error("两次输入的 PIN 不一致")
                    elif not (4 <= len(reg_pin or "") <= 8):
                        st.error("PIN 需要 6-8 位")
                    else:
                        st.session_state[K_AUTH] = {
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
        elif st.session_state.get(K_ACTIVATING):
            who = st.session_state[K_ACTIVATING]
            st.markdown(
                f"<div class='fz-hint'>👋 你好，{who}！首次登录请设置你的 PIN<br><span>只有你自己知道，管理员也看不到</span></div>",
                unsafe_allow_html=True,
            )
            _aerr = st.session_state.pop(K_ACT_ERR, None)
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
                if st.form_submit_button("设置 PIN 并进入", width="stretch"):
                    if act_pin != act_pin2:
                        st.error("两次输入的 PIN 不一致")
                    elif not (4 <= len(act_pin or "") <= 8):
                        st.error("PIN 需要 6-8 位")
                    else:
                        st.session_state[K_AUTH] = {
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
                st.session_state.pop(K_ACTIVATING, None)
                st.rerun()
        else:
            _lerr = st.session_state.pop(K_LOGIN_ERR, None)
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
                if st.form_submit_button("登 录", width="stretch"):
                    nm = login_name.strip()
                    if not nm or not login_pin:
                        st.error("请输入账号和密码")
                    else:
                        st.session_state[K_AUTH] = {
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


def require_user() -> str:
    """认证门闸：未登录则渲染登录页并 st.stop()；通过则返回用户名（含会话首同步）。

    fail-closed：默认 sheets 模式，secrets 缺失/损坏即 st.stop()；
    只有显式 DCA_AUTH_MODE=local 才进单机模式。
    """
    # ---- 认证模式（fail-closed）：默认 sheets；只有显式 DCA_AUTH_MODE=local 才进单机模式 ----
    # 原则：安全策略必须是显式声明的，「读不到凭据」永远不等于「不需要登录」。
    auth_mode = os.environ.get("DCA_AUTH_MODE", "sheets").strip().lower()
    user = "local"
    if auth_mode == "local":
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
        if K_USER not in st.session_state:
            _login_ph = (
                st.empty()
            )  # 登录页统一挂载点：每趟运行都在门闸首位创建，保证 delta 路径稳定
            _auth = st.session_state.get(K_AUTH)
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
                    except Exception as e:
                        # 这里一律塌缩成"网络异常"，异常对象不留痕就永远分不清凭据过期/配额撞满/网络抖动
                        _log.error("login_error name=%s err=%s", _auth.get("name"), e)
                        _status, _canon, _fresh = "error", None, None
                    if _fresh is not None:
                        st.session_state[K_NAMES] = _fresh  # 顺手刷新会话名单缓存
                    st.session_state.pop(K_AUTH, None)
                    if _status == "ok" and _canon:
                        st.session_state[K_USER] = _canon
                        show_auth_mask(
                            "正在登录",
                            [("验证账号", "done"), ("同步云端数据", "on")],
                            ph=_m,
                        )
                        try:
                            storage.sync_local(_canon)
                        except Exception as e:  # 同步失败不阻塞进入，侧栏🔄可重同步
                            _log.warning(
                                "post_login_sync_failed user=%s err=%s", _canon, e
                            )
                        st.session_state[K_SYNCED] = True
                    elif _status == "pending":
                        st.session_state[K_ACTIVATING] = _canon
                    elif _status == "no_user":
                        st.session_state[K_LOGIN_ERR] = "账号不存在，请联系管理员开通"
                    elif _status == "bad_pin":
                        st.session_state[K_LOGIN_ERR] = "账号或密码不对"
                    elif _status == "locked":
                        st.session_state[K_LOGIN_ERR] = (
                            "失败次数过多，账号已锁定，请 15 分钟后重试"
                        )
                    else:
                        st.session_state[K_LOGIN_ERR] = "网络异常，请稍后重试"
                    if _status in ("no_user", "bad_pin", "locked"):
                        _log.warning(
                            "auth_denied name=%s status=%s", _auth.get("name"), _status
                        )
                    st.rerun()
                elif _stage == "activate":
                    _m = show_auth_mask(
                        "正在设置 PIN", [("写入云端", "on"), ("同步云端数据", "off")]
                    )
                    try:
                        _ok, _msg = storage.set_pin(
                            _auth["who"], _auth["pin"], _auth["pin2"]
                        )
                    except Exception as e:
                        _log.error("set_pin_error user=%s err=%s", _auth.get("who"), e)
                        _ok, _msg = False, "网络异常，请稍后重试"
                    st.session_state.pop(K_AUTH, None)
                    if _ok:
                        st.session_state.pop(K_ACTIVATING, None)
                        st.session_state[K_USER] = _auth["who"]
                        show_auth_mask(
                            "正在设置 PIN",
                            [("写入云端", "done"), ("同步云端数据", "on")],
                            ph=_m,
                        )
                        try:
                            storage.sync_local(_auth["who"])
                        except Exception as e:  # 同步失败不阻塞进入
                            _log.warning(
                                "post_setpin_sync_failed user=%s err=%s",
                                _auth.get("who"),
                                e,
                            )
                        st.session_state[K_SYNCED] = True
                    else:
                        st.session_state[K_ACT_ERR] = (
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
                            st.session_state[K_NAMES] = _fresh
                            _ok, _msg = False, "系统已有账号，请直接登录"
                        else:
                            _ok, _msg = storage.create_user(
                                _auth["name"], _auth["pin"], _auth["pin2"], role="admin"
                            )
                    except Exception as e:
                        _log.error("bootstrap_error name=%s err=%s", _auth.get("name"), e)
                        _ok, _msg = False, "网络异常，请稍后重试"
                    st.session_state.pop(K_AUTH, None)
                    if _ok:
                        st.session_state[K_NAMES] = [_auth["name"]]
                        st.session_state[K_USER] = _auth["name"]
                        show_auth_mask(
                            "正在创建账号",
                            [("创建管理员账号", "done"), ("同步云端数据", "on")],
                            ph=_m,
                        )
                        try:
                            storage.sync_local(_auth["name"])
                        except Exception as e:  # 同步失败不阻塞进入
                            _log.warning(
                                "post_bootstrap_sync_failed user=%s err=%s",
                                _auth.get("name"),
                                e,
                            )
                        st.session_state[K_SYNCED] = True
                    elif _msg == "系统已有账号，请直接登录":
                        st.session_state[K_LOGIN_ERR] = (
                            _msg  # 名单已刷新，下一趟进登录页报错
                        )
                    else:
                        st.session_state[K_BOOT_ERR] = _msg
                    st.rerun()
                else:
                    st.session_state.pop(K_AUTH, None)  # 未知阶段：丢弃，回登录页
                    st.rerun()
            # —— 登录页渲染：名单走会话缓存，本页运行零网络请求 ——
            names = st.session_state.get(K_NAMES)
            if names is None:
                try:
                    names = storage.list_users()  # 仅每会话首次加载触网一次
                except Exception as e:
                    # 读不出名单绝不能渲染登录/自举表单——否则访客看到的就是"创建管理员"（fail-closed）
                    _log.error("user_list_unavailable_login_blocked err=%s", e)
                    st.error("☁️ 云端存储暂时不可用，请稍后刷新重试。")
                    st.stop()
                st.session_state[K_NAMES] = names
            # 登录页整体挂进固定容器：提交趟/校验趟里容器保持为空 → 上一趟登录页被整体摘除
            with _login_ph.container():
                _render_login_page(names, _login_ph)
            st.stop()
        user = st.session_state[K_USER]
        if not st.session_state.get(K_SYNCED):
            _ld = show_sync_mask("正在同步云端数据…", "首次进入稍等几秒")
            try:
                storage.sync_local(user)  # 每会话首次进入同步一次云端数据到本地缓存
            except Exception as e:
                # 同步失败不阻塞进入（侧栏🔄可重同步），但必须可见——否则建议会基于陈旧缓存静默出错
                _log.warning("session_sync_failed user=%s err=%s", user, e)
                st.warning("⚠️ 云端同步失败，本次建议基于本地缓存，可能不是最新。请稍后点侧栏 🔄 重试。")
            st.session_state[K_SYNCED] = True
            _ld.empty()
    return user

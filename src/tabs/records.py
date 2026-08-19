# -*- coding: utf-8 -*-
"""Tab3 记账：回报成交（buy/sell）+ 主动跳过（observe），复述确认后落库。

写链：session_state["pending_tx"]/["pending_obs"] 暂存 → 复述确认 → storage.append_row。
"""
from __future__ import annotations

import json
from datetime import date

import streamlit as st

import storage


def render(tab, result: dict, dec: dict, assets: dict, user: str):
    with tab:
        st.subheader("记录实际成交（买入/卖出）")
        st.caption(
            "流程：填写 → 复述确认 → 写入 transactions.csv（只追加）。建议与成交严格分离。"
        )
        with st.form("tx_form"):
            c1, c2, c3 = st.columns(3)
            tx_date = c1.text_input("日期", value=str(date.today()))
            tx_asset = c2.selectbox(
                "资产", list(assets.keys()), format_func=lambda k: assets[k]["name_cn"]
            )
            tx_action = c3.selectbox("类型", ["buy", "sell"])
            default_sym = assets[tx_asset]["symbol"]
            c4, c5, c6 = st.columns(3)
            tx_symbol = c4.text_input("代码", value=default_sym)
            tx_amount = c5.number_input("金额 RMB", min_value=0.0, step=100.0)
            tx_price = c6.number_input(
                "净值价格 U", min_value=0.0, step=0.01, format="%.4f"
            )
            c7, c8, c9 = st.columns(3)
            default_fx = (
                result.get("usdtcny")
                if assets[tx_asset].get("fx_mode") == "usdt"
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
                f"请确认写入：{p['date']} {p['action']} {assets[p['asset']]['name_cn']}（{p['symbol']}）"
                f"｜金额 ¥{p['amount_rmb']:,.2f}｜净值 {p['price']} U｜数量 {p['shares']}｜汇率 {p['fx_rate']}｜手续费 ¥{p['fee_rmb']}｜{p['notes']}"
            )
            b1, b2 = st.columns(2)
            if b1.button("✅ 确认写入", use_container_width=True):
                try:
                    storage.append_row("transactions", user, p)
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
                        "observations", user, st.session_state.pop("pending_obs")
                    )
                except Exception as _e:
                    st.error(f"写入失败：云端存储暂时不可用（{_e}）。历史数据未被覆盖，请稍后重试。")
                else:
                    st.cache_data.clear()
                    st.success("已写入观察记录")
                    st.rerun()

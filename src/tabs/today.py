# -*- coding: utf-8 -*-
"""Tab1 今日模拟：建议金额/部署系数/可用池/每日基准 + 复盘 + 宽表 + 行情评分。

数据全部显式收参，不读 app.py 模块级全局；parse_wide_table 来自 src.services.model。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ..services.model import parse_wide_table


def render(tab, result: dict, dec: dict, ms: dict, assets: dict):
    with tab:
        if dec.get("degraded"):
            _fr = dec.get("freshness") or {}
            st.error(
                f"⚠️ 行情不可用于决策 → 本次不出金额：{_fr.get('reason') or '信号标的无可用行情'}。"
                "持仓与历史照常展示；请先排查数据源，不要照旧价下单。"
            )
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
        for key, info in assets.items():
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

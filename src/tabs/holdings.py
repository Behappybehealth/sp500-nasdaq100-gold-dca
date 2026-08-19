# -*- coding: utf-8 -*-
"""Tab2 持仓与曲线：组合市值 vs 累计投入 / 权重对比 / 近一年价格走势。

数据全部显式收参，不读 app.py 模块级全局；portfolio_curve / load_price_series 来自 src.services.curves。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from ..context import Paths
from ..services.curves import load_price_series, portfolio_curve


def render(tab, result: dict, pf: dict, assets: dict, paths: Paths):
    with tab:
        st.subheader("组合市值 vs 累计投入")
        curve = portfolio_curve(result, paths)
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
        for key, info in assets.items():
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
        series = load_price_series(paths)
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

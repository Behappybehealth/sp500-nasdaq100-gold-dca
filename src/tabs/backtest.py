# -*- coding: utf-8 -*-
"""Tab5 回测结果：5 段静态报告，全部读 backtest/*.json（BUG-025 单一供数）。

BUG-020 刀 4 从 app.py:923-1155 原样搬入；仅把全局 BACKTEST_DIR 换成显式参数，
_load_json 来自 src.services.curves。内部段界见 docs/ARCHITECTURE-DETAIL.md §10。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from ..services.curves import _load_json


def render(tab, backtest_dir: Path):
    with tab:
        st.subheader("历史回测结果")
        st.caption(
            "以下回测均基于历史数据滚动测算，不代表未来表现。数据窗口、定投频率、金额各标的略有差异，横向仅供参考。"
        )

        # ========== ① 三策略对比（组合级） ==========
        st.markdown("---")
        st.markdown("### 一、三策略对比（组合级，2021-08 → 2026-08，1254 条任意起点路径）")
        cmp_file = backtest_dir / "results_compare3.json"
        single_file = backtest_dir / "results_single_compare.json"
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
        rolling_file = backtest_dir / "results_rolling.json"
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

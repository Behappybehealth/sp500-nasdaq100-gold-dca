"""引擎纯函数回归（离线，固定输入 → 固定输出）。

覆盖 `scripts/dca_calculator.py` 的 7 个纯函数。选这 7 个是因为它们决定"今天买多少、
按什么比例买"，且不碰网络、不碰全局状态——正好是最值得也最容易钉死的一层。

期望值全部手算后与实装比对过一致（不是把实装输出照抄回来当期望）。
"""

from __future__ import annotations

import math
from datetime import date

import dca_calculator as eng
import pytest

W = {"value": 0.50, "trend": 0.25, "momentum": 0.15,
     "heat": 0.45, "heat_quad": 0.20, "volatility": 0.15}


# ============================================================
# metrics_from_closes —— 指标计算
# ============================================================

RAMP = [100.0 + i for i in range(300)]  # 单调上行 100…399


def test_metrics_ramp_basics():
    """单调上行序列：收益率/均线/均线偏离按定义算，latest 与序列末值分开生效。"""
    m = eng.metrics_from_closes(RAMP, 400.0, "2025-01-01", "2026-08-20")
    assert m["latest_price"] == 400.0
    assert m["history_start"] == "2025-01-01"
    assert m["return_1d"] == pytest.approx(399 / 398 - 1)
    assert m["return_252d"] == pytest.approx(399 / 147 - 1)
    # 均线用序列自身（末 20 个 380…399 的均值），偏离用传入的 latest
    assert m["ma_20"] == pytest.approx(389.5)
    assert m["ma_20_deviation"] == pytest.approx(400.0 / 389.5 - 1)


def test_metrics_rsi_all_gains_is_100():
    """全涨无跌 → avg_loss 为 0，RSI 必须给 100 而不是除零崩掉。"""
    assert eng.metrics_from_closes(RAMP, 400.0, "a", "b")["rsi_14"] == 100.0


def test_metrics_252d_range():
    """252 日高低区间：高点取序列末 252 段，latest 高于高点时回撤为正。"""
    m = eng.metrics_from_closes(RAMP, 400.0, "a", "b")
    assert m["drawdown_from_252d_high"] == pytest.approx(400.0 / 399 - 1)
    assert m["position_in_252d_range"] == pytest.approx((400.0 - 148) / (399 - 148))


def test_metrics_short_series_omits_keys():
    """样本不够的指标必须缺键，而不是拿残缺窗口硬算出一个假值。"""
    m = eng.metrics_from_closes([1.0, 2.0, 3.0, 4.0, 5.0], 5.0, "a", "b")
    assert set(m) == {"latest_price", "history_start", "history_end", "return_1d"}
    assert "ma_20" not in m and "rsi_14" not in m and "vol_20d_annualized" not in m


def test_metrics_volatility_is_annualized():
    """波动率按日收益标准差 × √252 年化；恒定涨幅序列波动应为 0。"""
    flat = [100.0 * 1.001**i for i in range(100)]  # 每日恒定 +0.1%
    m = eng.metrics_from_closes(flat, flat[-1], "a", "b")
    assert m["vol_20d_annualized"] == pytest.approx(0.0, abs=1e-12)
    assert m["ma_60_deviation"] == pytest.approx(flat[-1] / (sum(flat[-60:]) / 60) - 1)


# ============================================================
# load_cached_closes —— 缓存读取（脏数据必须被剔掉）
# ============================================================

def test_load_cached_closes_filters_dirty_rows(tmp_path):
    """close<=0 / 空日期 / 非数字都必须丢弃，不能污染价格序列。"""
    p = tmp_path / "X.csv"
    p.write_text(
        "date,close\n"
        "2026-08-17,100.5\n"
        "2026-08-18,0\n"        # 0 价：丢
        ",123.4\n"              # 空日期：丢
        "2026-08-19,abc\n"      # 非数字 → as_float 给 0 → 丢
        "2026-08-20,-5\n"       # 负价：丢
        "2026-08-21,200.25\n",
        encoding="utf-8",
    )
    assert eng.load_cached_closes(p) == {"2026-08-17": 100.5, "2026-08-21": 200.25}


def test_load_cached_closes_missing_file_is_empty(tmp_path):
    """缺文件返回空字典（触发全量重建），不是抛错。"""
    assert eng.load_cached_closes(tmp_path / "nope.csv") == {}


def test_load_cached_closes_tolerates_bom(tmp_path):
    """Excel 存过的 csv 带 BOM，首列名会变成 \\ufeffdate——必须仍能读出。"""
    p = tmp_path / "B.csv"
    p.write_bytes("date,close\n2026-08-17,1.5\n".encode("utf-8-sig"))
    assert eng.load_cached_closes(p) == {"2026-08-17": 1.5}


def test_load_cached_closes_last_wins_on_duplicate_date(tmp_path):
    """同日期重复行取后者（与增量写入"后抓的覆盖先抓的"一致）。"""
    p = tmp_path / "D.csv"
    p.write_text("date,close\n2026-08-17,1.0\n2026-08-17,2.0\n", encoding="utf-8")
    assert eng.load_cached_closes(p) == {"2026-08-17": 2.0}


# ============================================================
# monthly_budget_status —— 月度预算池
# ============================================================

def _tx(day: str, amount: float, action: str = "buy", asset: str = "sp500") -> eng.Transaction:
    return eng.Transaction(date=day, action=action, asset=asset, symbol="SPY", currency="U",
                           amount_rmb=amount, price=0.0, shares=0.0, fee_rmb=0.0,
                           fx_rate=7.0, notes="")


# 2026 年 8 月：1 号是周六，全月 21 个工作日；以 08-20（周四）为"今天"时含今天剩 8 个
AUG_TOTAL_TD = 21
AUG_20_REMAIN_TD = 8


def test_budget_counts_only_this_month_buys():
    """本月已投只算本月、只算非 sell；卖出与其他月份不得计入。"""
    txs = [_tx("2026-08-03", 1000.0), _tx("2026-08-10", 2000.0),
           _tx("2026-08-12", 5000.0, action="sell"), _tx("2026-07-31", 9000.0)]
    s = eng.monthly_budget_status(txs, 30000.0, date(2026, 8, 20))
    assert s["month"] == "2026-08"
    assert s["invested_this_month_rmb"] == 3000.0
    assert s["remaining_budget_rmb"] == 27000.0
    assert s["total_trading_days_in_month"] == AUG_TOTAL_TD
    assert s["remaining_trading_days"] == AUG_20_REMAIN_TD


def test_budget_default_start_forfeits_days_before_today():
    """不传 month_start_date 时 start=今天 → 今天之前的 13 个交易日份额作废。

    这是"月中才启动定投不追补"的直接后果：08-20 首次运行只拿到当月 8/21 的额度。
    """
    s = eng.monthly_budget_status([], 30000.0, date(2026, 8, 20))
    assert s["month_start_date"] == "2026-08-20"
    assert s["forfeited_rmb"] == pytest.approx(30000.0 * 13 / 21, abs=0.01)
    assert s["available_pool_rmb"] == pytest.approx(30000.0 * 8 / 21, abs=0.01)
    assert s["paced_amount_rmb"] == pytest.approx(s["available_pool_rmb"] / 8, abs=0.01)


def test_budget_explicit_start_forfeits_only_pre_start_days():
    """显式给启动日（08-10）→ 只作废 08-10 前的 5 个交易日，启动后跳过的日子自动摊入后续。"""
    s = eng.monthly_budget_status([], 30000.0, date(2026, 8, 20), month_start_date="2026-08-10")
    assert s["forfeited_rmb"] == pytest.approx(30000.0 * 5 / 21, abs=0.01)
    assert s["available_pool_rmb"] == pytest.approx(30000.0 * 16 / 21, abs=0.01)
    assert s["paced_amount_rmb"] == pytest.approx(s["available_pool_rmb"] / 8, abs=0.01)


def test_budget_pool_never_negative_when_overspent():
    """超投时可用池与剩余预算钳到 0，不得出现负数金额传给决策层。"""
    s = eng.monthly_budget_status([_tx("2026-08-05", 99999.0)], 30000.0, date(2026, 8, 20),
                                  month_start_date="2026-08-01")
    assert s["remaining_budget_rmb"] == 0.0
    assert s["available_pool_rmb"] == 0.0
    assert s["paced_amount_rmb"] == 0.0
    assert s["month_end_release_active"] is False  # 池空 → 不触发释放


def test_budget_month_end_release_window():
    """月末释放：剩余自然日 <= 窗口且池非空才触发，且带可读说明。"""
    early = eng.monthly_budget_status([], 30000.0, date(2026, 8, 20), month_start_date="2026-08-01")
    late = eng.monthly_budget_status([], 30000.0, date(2026, 8, 27), month_start_date="2026-08-01")
    assert early["remaining_days_in_month"] == 12 and early["month_end_release_active"] is False
    assert late["remaining_days_in_month"] == 5 and late["month_end_release_active"] is True
    assert early["month_end_release_note"] == ""
    assert "月末释放已触发" in late["month_end_release_note"]


def test_budget_december_rolls_to_next_year():
    """12 月的"下月 1 号"必须跨年到次年 1 月，否则剩余天数会算成负。"""
    s = eng.monthly_budget_status([], 30000.0, date(2026, 12, 20))
    assert s["month"] == "2026-12"
    assert s["remaining_days_in_month"] == 12  # 12-20 → 次年 01-01
    assert s["remaining_trading_days"] >= 1


def test_budget_remaining_trading_days_floor_is_one():
    """月末最后一天是周末时剩余交易日会算成 0——必须兜到 1，否则 paced 除零。"""
    s = eng.monthly_budget_status([], 30000.0, date(2026, 8, 30), month_start_date="2026-08-01")
    assert s["remaining_trading_days"] >= 1
    assert math.isfinite(s["paced_amount_rmb"])


# ============================================================
# xirr —— 年化收益率
# ============================================================

def test_xirr_single_year_10pct():
    """-1000 一年后收回 1100 → 年化 10%。"""
    r = eng.xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0)])
    assert r == pytest.approx(0.10, abs=1e-4)


def test_xirr_multi_flow_is_a_root_of_xnpv():
    """多笔不规则现金流：解出的 rate 必须让 xnpv 归零（这是 xirr 的定义）。"""
    flows = [(date(2024, 1, 15), -3000.0), (date(2024, 6, 1), -2000.0),
             (date(2025, 3, 20), -1500.0), (date(2026, 8, 20), 8200.0)]
    r = eng.xirr(flows)
    assert r is not None
    assert eng.xnpv(r, sorted(flows)) == pytest.approx(0.0, abs=1e-2)


def test_xirr_needs_both_signs():
    """只有流出或只有流入 → 无解，必须返回 None 而不是编一个数。"""
    assert eng.xirr([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), -200.0)]) is None
    assert eng.xirr([(date(2025, 1, 1), 100.0), (date(2026, 1, 1), 200.0)]) is None


def test_xirr_needs_two_flows():
    """不足两笔（含金额为 0 被过滤掉的情况）→ None。"""
    assert eng.xirr([(date(2025, 1, 1), -100.0)]) is None
    assert eng.xirr([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), 0.0)]) is None
    assert eng.xirr([]) is None


def test_xirr_total_loss_is_negative():
    """本金几乎打光 → 年化必须是显著负值。"""
    r = eng.xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 100.0)])
    assert r is not None and r < -0.85


# ============================================================
# asset_score —— 吸引力评分（本策略的核心校准）
# ============================================================

HOT = {"latest_price": 100.0, "drawdown_from_252d_high": -0.005, "position_in_252d_range": 1.0,
       "return_20d": 0.10, "return_60d": 0.25, "return_120d": 0.40,
       "ma_60_deviation": 0.15, "ma_252_deviation": 0.25,
       "rsi_14": 85.0, "vol_20d_annualized": 0.35}

KNIFE = {"latest_price": 100.0, "drawdown_from_252d_high": -0.25, "position_in_252d_range": 0.05,
         "return_20d": -0.10, "return_60d": -0.15, "return_120d": -0.20,
         "ma_60_deviation": -0.10, "ma_252_deviation": -0.05,
         "rsi_14": 25.0, "vol_20d_annualized": 0.30}

DIP = {"latest_price": 100.0, "drawdown_from_252d_high": -0.10, "position_in_252d_range": 0.5,
       "return_20d": -0.02, "return_60d": 0.03, "return_120d": 0.06,
       "ma_60_deviation": 0.02, "ma_252_deviation": 0.05,
       "rsi_14": 45.0, "vol_20d_annualized": 0.15}


def test_score_none_without_price():
    """没有 latest_price 就是没行情，只能返回 score=None（下游据此走"数据不可用"）。"""
    assert eng.asset_score({}, W) == {"score": None}
    assert eng.asset_score({"return_20d": 0.1}, W) == {"score": None}


def test_score_overheated_is_strongly_negative():
    """极端过热（RSI 85 + 区间顶 + 高波动）必须压成显著负分 → 不买。"""
    s = eng.asset_score(HOT, W)
    assert s["heat"] == 1.0 and s["vol_penalty"] == 1.0
    assert s["score"] == pytest.approx(-0.7708, abs=1e-4)
    assert s["score"] < -0.5


def test_score_trend_and_momentum_are_capped():
    """趋势封顶 0.4、动量封顶 0.5：只防御不追高，涨得再猛也不加分。"""
    s = eng.asset_score(HOT, W)
    assert s["trend"] == 0.4
    assert s["momentum"] == 0.5


def test_score_falling_knife_value_is_gated_by_trend():
    """深回撤 + 趋势破坏 = 飞刀：回撤价值被趋势健康度门控，总分仍为负。"""
    s = eng.asset_score(KNIFE, W)
    assert s["value"] == pytest.approx(0.4107, abs=1e-4)  # 满值 1.0 被门控砍到 0.41
    assert s["value"] < 1.0
    assert s["score"] < 0


def test_score_healthy_dip_is_positive():
    """趋势健康 + 温和回撤 + 不过热 = 该买，总分为正。"""
    s = eng.asset_score(DIP, W)
    assert s["score"] == pytest.approx(0.3567, abs=1e-4)
    assert s["score"] > 0


def test_score_missing_indicators_fall_back_to_neutral():
    """指标缺失时按中性值兜底（RSI 50 / 区间中位 / 波动 0.15），不得崩。"""
    s = eng.asset_score({"latest_price": 100.0}, W)
    assert s["score"] is not None
    assert s["heat"] == 0.0 and s["vol_penalty"] == 0.0


def test_score_ordering_dip_beats_knife_beats_hot():
    """三种形态的排序是策略意图本身：健康回调 > 飞刀 > 过热。"""
    assert (eng.asset_score(DIP, W)["score"]
            > eng.asset_score(KNIFE, W)["score"]
            > eng.asset_score(HOT, W)["score"])


# ============================================================
# score_based_weights —— 权重倾斜
# ============================================================

def _scores(sp: float, ndx: float, gold: float, trend: float = 0.0) -> dict:
    return {"sp500": {"score": sp, "trend": trend},
            "nasdaq100": {"score": ndx, "trend": trend},
            "gold": {"score": gold, "trend": 0.0}}


def test_weights_zero_scores_give_neutral(assets, model):
    """评分全 0 → 权重就是中性权重 35/45/20，倾斜项不得凭空生效。"""
    w = eng.score_based_weights(_scores(0.0, 0.0, 0.0), assets, model)
    assert w == {"sp500": pytest.approx(0.35), "nasdaq100": pytest.approx(0.45), "gold": pytest.approx(0.20)}


def test_weights_always_sum_to_one(assets, model):
    """任何评分组合下权重必须归一——它直接乘金额，和不为 1 就是多花或少花钱。"""
    for sc in [_scores(0.0, 0.0, 0.0), _scores(-0.8, 0.9, -0.5, trend=0.4),
               _scores(0.9, 0.9, 0.9), _scores(-1.0, -1.0, -1.0, trend=-1.0)]:
        assert sum(eng.score_based_weights(sc, assets, model).values()) == pytest.approx(1.0)


def test_weights_gold_gets_defense_boost_when_equity_trend_weak(assets, model):
    """权益趋势走弱 → 黄金拿防御加成，权重高于中性 20%。"""
    w = eng.score_based_weights(_scores(0.0, 0.0, 0.0, trend=-0.5), assets, model)
    assert w["gold"] > 0.20
    assert w["gold"] == pytest.approx(0.2410, abs=1e-3)


def test_weights_no_defense_boost_when_equity_trend_healthy(assets, model):
    """权益趋势正常时黄金不该加成（防御加成只在 -eq_trend > 0 时生效）。"""
    w = eng.score_based_weights(_scores(0.0, 0.0, 0.0, trend=0.3), assets, model)
    assert w["gold"] == pytest.approx(0.20)


def test_weights_stay_inside_bounds(assets, model):
    """极端评分下权重必须严格落在 min/max 区间内，且仍然和为 1。

    两个不变量同时成立才有意义：`max_weight` 是风控上限（"标普最多 55%"），
    和为 1 决定当日总额不多花也不少花。断言不留容差——留容差就等于承认上限可破。
    """
    w = eng.score_based_weights(_scores(-0.8, 0.9, -0.5, trend=0.4), assets, model)
    for key, info in assets.items():
        assert w[key] >= info["min_weight"], f"{key} 跌破下限"
        assert w[key] <= info["max_weight"], f"{key} 突破上限"
    assert sum(w.values()) == pytest.approx(1.0)


def test_weights_bounds_hold_across_score_grid(assets, model):
    """扫评分网格：任何组合都不得越界。

    单点断言挡不住这个缺陷——旧实现在 61% 的评分组合上越界，却恰好在
    `test_weights_stay_inside_bounds` 那一点只差 6e-05，被容差吞掉了（BUG-033）。
    最坏的一例是突破上限 9.2e-03（sp500 到 0.5592），不在被抽查的那点上。
    """
    grid = [i / 5 for i in range(-5, 6)]  # -1.0 .. 1.0 步长 0.2
    for sp in grid:
        for ndx in grid:
            for gold in grid:
                for trend in (-1.0, 0.0, 1.0):
                    w = eng.score_based_weights(_scores(sp, ndx, gold, trend=trend), assets, model)
                    label = f"sp={sp} ndx={ndx} gold={gold} trend={trend}"
                    for key, info in assets.items():
                        assert w[key] >= info["min_weight"], f"{key} 跌破下限（{label}）"
                        assert w[key] <= info["max_weight"], f"{key} 突破上限（{label}）"
                    assert sum(w.values()) == pytest.approx(1.0), f"和不为 1（{label}）"


def test_weights_missing_score_treated_as_zero(assets, model):
    """某资产没评分（行情缺失）时按 0 处理，不得抛 TypeError。"""
    w = eng.score_based_weights({"sp500": {"score": None, "trend": None}}, assets, model)
    assert sum(w.values()) == pytest.approx(1.0)


# ============================================================
# build_decision —— 金额与比例的最终合成
# ============================================================

def _markets(*, hot=False, knife=False, dip=False) -> dict:
    src = HOT if hot else KNIFE if knife else DIP
    return {"^GSPC": dict(src), "^NDX": dict(src), "GC=F": dict(src)}


def _status(pool: float, paced: float, *, release: bool = False, remaining_td: int = 8) -> dict:
    return {"remaining_budget_rmb": pool, "available_pool_rmb": pool,
            "paced_amount_rmb": paced, "remaining_trading_days": remaining_td,
            "month_end_release_active": release}


def test_decision_no_market_data_gives_zero(assets, model):
    """三个信号标的全无行情 → 金额 0 + "数据不可用"，绝不拿空数据出钱数。"""
    d = eng.build_decision({}, assets, model, _status(20000.0, 2000.0), None)
    assert d["suggested_amount_rmb"] == 0.0
    assert d["level_label"] == "数据不可用"
    assert d["deploy_multiplier"] == 0.0
    assert sum(d["weights"].values()) == pytest.approx(1.0)  # 仍给中性比例供展示


def test_decision_healthy_dip_buys_more_than_base(assets, model):
    """健康回调（评分 +0.3567）→ 部署系数 > 1，金额高于基准。"""
    d = eng.build_decision(_markets(dip=True), assets, model, _status(20000.0, 2000.0), None)
    assert d["equity_opportunity"] == pytest.approx(0.3567, abs=1e-4)
    assert d["deploy_multiplier"] == pytest.approx(1.0 + 1.1 * 0.3567, abs=1e-3)
    assert d["base_amount_rmb"] == 2000.0
    # rel 而非 abs：输出里的 deploy_multiplier 是 round(…, 4)，金额用的是未舍入值
    assert d["suggested_amount_rmb"] == pytest.approx(2000.0 * d["deploy_multiplier"], rel=1e-4)
    assert d["amount_source"] == "model"


def test_decision_overheat_with_strong_trend_only_trims(assets, model):
    """过热但趋势仍强 → 压到"小额试探"（≈基准 15%），不是归零。

    评分 -0.7708 → 部署系数 0.1521，刚好高过 skip_below 0.15。这不是巧合：
    趋势/动量封顶后仍贡献 +0.175，涨势还在时策略选择继续小额参与而非清零。
    """
    d = eng.build_decision(_markets(hot=True), assets, model, _status(20000.0, 2000.0), None)
    assert d["equity_opportunity"] == pytest.approx(-0.7708, abs=1e-4)
    assert d["deploy_multiplier"] == pytest.approx(0.1521, abs=1e-4)
    assert d["suggested_amount_rmb"] == pytest.approx(304.24, abs=0.01)
    assert d["level_label"] == "小额试探"


def test_decision_overheat_with_dead_trend_stops_buying(assets, model):
    """过热且趋势转平（热度还在、涨势没了）→ 部署系数归零，今日不买。

    与上一条成对：过热本身不足以停手，"过热 + 趋势失守"才停。skip_below 的
    实际触发线是权益综合评分 < -0.7727。
    """
    flat_hot = {"latest_price": 100.0, "drawdown_from_252d_high": 0.0,
                "position_in_252d_range": 1.0, "return_20d": 0.10,
                "return_60d": 0.0, "return_120d": 0.0,
                "ma_60_deviation": 0.0, "ma_252_deviation": 0.0,
                "rsi_14": 85.0, "vol_20d_annualized": 0.35}
    d = eng.build_decision({"^GSPC": dict(flat_hot), "^NDX": dict(flat_hot), "GC=F": dict(flat_hot)},
                           assets, model, _status(20000.0, 2000.0), None)
    assert d["equity_opportunity"] == pytest.approx(-0.9429, abs=1e-4)
    assert d["deploy_multiplier"] == 0.0
    assert d["suggested_amount_rmb"] == 0.0
    assert d["level_label"] == "今日不买，延后观察"


def test_decision_user_amount_overrides_base(assets, model):
    """用户手填金额 → 基准换成它，amount_source 标 user_input（Web 侧第二趟就是这条路）。"""
    d = eng.build_decision(_markets(dip=True), assets, model, _status(50000.0, 2000.0), 8000.0)
    assert d["base_amount_rmb"] == 8000.0
    assert d["amount_source"] == "user_input"
    assert d["suggested_amount_rmb"] == pytest.approx(8000.0 * d["deploy_multiplier"], rel=1e-4)


def test_decision_amount_capped_by_available_pool(assets, model):
    """建议金额永不超过本月可用池——这是不能超预算的硬闸。"""
    d = eng.build_decision(_markets(dip=True), assets, model, _status(500.0, 2000.0), 99999.0)
    assert d["suggested_amount_rmb"] == 500.0


def test_decision_empty_pool_gives_zero_with_reason(assets, model):
    """池已用完 → 金额 0，且 reason 里要说清是"用完了"而不是"不该买"。"""
    d = eng.build_decision(_markets(dip=True), assets, model, _status(0.0, 0.0), None)
    assert d["suggested_amount_rmb"] == 0.0
    assert "本月可用池已用完" in d["reason"]


def test_decision_month_end_release_lifts_to_paced(assets, model):
    """月末释放窗口内金额被抬到至少等于日均节奏值（否则月末结余投不出去）。"""
    weak = {"^GSPC": dict(DIP), "^NDX": dict(DIP), "GC=F": dict(DIP)}
    d = eng.build_decision(weak, assets, model, _status(20000.0, 5000.0, release=True), 100.0)
    assert d["suggested_amount_rmb"] >= 5000.0
    assert "月末释放中" in d["reason"]


def test_decision_reason_lists_all_three_assets(assets, model):
    """reason 是给人看的唯一解释，三个资产的评分与前三贡献项都要在。"""
    d = eng.build_decision(_markets(dip=True), assets, model, _status(20000.0, 2000.0), None)
    for name in ("标普500", "纳指100", "黄金"):
        assert name in d["reason"]
    assert "部署系数" in d["reason"] and "可用池" in d["reason"]


def test_decision_deploy_multiplier_capped_at_max(assets, model):
    """部署系数封顶 deploy_max，再好的评分也不能无限加仓。"""
    perfect = {"latest_price": 100.0, "drawdown_from_252d_high": -0.30,
               "position_in_252d_range": 0.1, "return_20d": 0.0,
               "return_60d": 0.05, "return_120d": 0.10,
               "ma_60_deviation": 0.05, "ma_252_deviation": 0.05,
               "rsi_14": 50.0, "vol_20d_annualized": 0.10}
    d = eng.build_decision({"^GSPC": perfect, "^NDX": perfect, "GC=F": perfect},
                           assets, model, _status(99999.0, 2000.0), None)
    assert d["deploy_multiplier"] <= model["deploy_max"]


def test_decision_output_shape_is_stable(assets, model):
    """输出键集合是 UI 与 Skill 的解包契约，少一个键就是线上 KeyError。"""
    d = eng.build_decision(_markets(dip=True), assets, model, _status(20000.0, 2000.0), None)
    assert set(d) == {"scores", "weights", "level_label", "deploy_multiplier",
                      "equity_opportunity", "base_amount_rmb", "available_pool_rmb",
                      "suggested_amount_rmb", "amount_source", "reason"}
    assert set(d["scores"]) == set(assets)
    assert set(d["weights"]) == set(assets)

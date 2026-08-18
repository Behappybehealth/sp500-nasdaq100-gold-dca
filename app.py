# -*- coding: utf-8 -*-
"""动态定投模拟决策台（Streamlit 网页版）

数据与策略完全复用 sp500-nasdaq100-gold-dca skill：
- 行情/决策：子进程调用 scripts/dca_calculator.py（自带缓存、评分模型、月末释放）
- 记账：复述确认后追加 transactions / observations（配置 secrets 后用 Google Sheets 云端存储并按用户隔离；未配置时回退本地 CSV，只追加不覆盖）

启动：streamlit run app.py
多用户：配置 .streamlit/secrets.toml 的 [connections.gsheets] 后自动启用云端存储 + 名字/PIN 门闸
"""


import storage  # 存储层：Google Sheets 优先，本地 CSV 回退
import streamlit as st

from src.context import build_paths
from src.ui import auth, sidebar
from src.ui.styles import inject_css
from src.tabs import backtest, history, holdings, records, strategy_doc, today

# ---- 路径：代码 vs 数据分离（启动逻辑已收编 src/context.py，BUG-020 刀 2）----
_paths = build_paths()
# 过渡桥：剩余模块级全局供 storage.init 与各 tab render 调用（BASE/TX_CSV/CONFIG 死引用已摘除）
CODE_DIR = _paths.code_dir
DATA_DIR = _paths.data_dir
ASSETS = _paths.assets
BACKTEST_DIR = _paths.backtest_dir

storage.init(DATA_DIR)

st.set_page_config(page_title="模拟定投决策台", layout="wide", page_icon="📈")


# ---- 全局样式与加载组件 ----
# CSS 已搬至 src/ui/styles.py（BUG-020 刀 3）；遮罩的不透明 background 是冻屏坑防线，详见该文件头注。
inject_css()

# ---- 认证门闸（BUG-020 刀 7）：本体在 src/ui/auth.py（登录页/遮罩/会话首同步；未登录 st.stop()）----
CURRENT_USER = auth.require_user()

# ---- 服务函数已搬至 src/services/（BUG-020 刀 2）：
# run_model / parse_wide_table → services/model.py；fetch_xau_spot / fetch_btc → services/quotes.py；
# _load_json / load_price_series / portfolio_curve → services/curves.py。调用点显式传 _paths。

# ---------------- 侧边栏 ----------------
# 侧栏已搬至 src/ui/sidebar.py（BUG-020 刀 6：render() 返回 Decision，收口 result/dec/ms/pf）
_decision = sidebar.render(_paths, CURRENT_USER)
result, dec, ms, pf = _decision.result, _decision.dec, _decision.ms, _decision.pf

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

# ---- 五个只读 tab 已搬至 src/tabs/（BUG-020 刀 4）：today/holdings/history/backtest/strategy_doc ----
today.render(tab1, result, dec, ms, ASSETS)

# tab2 已搬至 src/tabs/holdings.py（BUG-020 刀 4）
holdings.render(tab2, result, pf, ASSETS, _paths)

# tab3 已搬至 src/tabs/records.py（BUG-020 刀 5：写链单独成刀）
records.render(tab3, result, dec, ASSETS, CURRENT_USER)

# tab4 已搬至 src/tabs/history.py（BUG-020 刀 4）
history.render(tab4, CURRENT_USER)

# tab5 已搬至 src/tabs/backtest.py（BUG-020 刀 4）
backtest.render(tab5, BACKTEST_DIR)

# tab6 已搬至 src/tabs/strategy_doc.py（BUG-020 刀 4）
strategy_doc.render(tab6, CODE_DIR)

"""动态定投模拟决策台（Streamlit 网页版）

数据与策略完全复用 sp500-nasdaq100-gold-dca skill：
- 行情/决策：子进程调用 scripts/dca_calculator.py（自带缓存、评分模型、月末释放）
- 记账：复述确认后追加 transactions / observations（配置 secrets 后用 Google Sheets 云端存储并按用户隔离；未配置时回退本地 CSV，只追加不覆盖）

启动：streamlit run app.py
多用户：配置 .streamlit/secrets.toml 的 [connections.gsheets] 后自动启用云端存储 + 名字/PIN 门闸
"""


import streamlit as st

import storage  # 存储层：Google Sheets 优先，本地 CSV 回退
from src.context import build_paths
from src.obs import setup_logging
from src.tabs import backtest, history, holdings, records, strategy_doc, today
from src.ui import auth, sidebar
from src.ui.styles import inject_css

# ---- 路径：代码 vs 数据分离，由 src/context.build_paths 装配 ----
_paths = build_paths()
# 模块级别名：供 storage.init 与各 tab render 传参
CODE_DIR = _paths.code_dir
DATA_DIR = _paths.data_dir
ASSETS = _paths.assets
BACKTEST_DIR = _paths.backtest_dir

# ---- 运行日志：必须在 storage.init 之前，否则首次读写的失败无处落（src/obs.py，幂等）----
setup_logging(CODE_DIR / "logs")

storage.init(DATA_DIR)

st.set_page_config(page_title="模拟定投决策台", layout="wide", page_icon="📈")


# ---- 全局样式（src/ui/styles.py；遮罩的不透明 background 是冻屏坑防线，详见该文件头注）----
inject_css()

# ---- 认证门闸：src/ui/auth.py（登录页/遮罩/会话首同步；未登录 st.stop()）----
CURRENT_USER = auth.require_user()

# ---------------- 侧边栏：渲染 + 跑模型（src/ui/sidebar.py）----------------
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

today.render(tab1, result, dec, ms, ASSETS)

holdings.render(tab2, result, pf, ASSETS, _paths, CURRENT_USER)

records.render(tab3, result, dec, ASSETS, CURRENT_USER)

history.render(tab4, CURRENT_USER)

backtest.render(tab5, BACKTEST_DIR)

strategy_doc.render(tab6, CODE_DIR)

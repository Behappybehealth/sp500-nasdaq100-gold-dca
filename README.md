# 模拟定投决策台（Streamlit）

面向小团队/家庭试用的 DCA 定投回测与决策工具。数据层已迁移到 Google Sheets（按用户名+PIN 隔离），未配置云端 secrets 时自动回退本地 CSV 模式。

> 合规提示：本项目是回测/计算器工具，不构成投资建议。

## 本地运行

```bash
C:\Python314\python.exe -m streamlit run app.py --server.headless true --server.port 8501
```

备用公网入口（本机常开时）：

```bash
C:\Users\xiezhibo\start-dca-tunnel.bat
```

## Streamlit Community Cloud 部署

1. 把本目录推送到 GitHub（建议私有仓库）。
2. 打开 https://share.streamlit.io/ → New app。
3. 选择仓库、分支，`Main file path` 填 `app.py`。
4. 在应用后台 `Settings → Secrets` 粘贴 `.streamlit/secrets.toml` 同内容（不要提交真实 secrets 到 Git）。
5. Deploy 后验证：注册/激活用户 → 写入一条测试记录 → 删除测试记录。

所需 secrets 结构见 `.streamlit/secrets.toml.example`。

## 仓库内容说明

- `app.py`：Streamlit 主应用。
- `storage.py`：Google Sheets 优先、本地 CSV 回退的存储层。
- `scripts/dca_calculator.py`：策略/行情计算引擎。
- `data/config.json`：默认资产配置，需要入库。
- `data/market_history/*.csv`：行情缓存，需要入库以避免首次部署冷启动过慢。
- `data/transactions.csv`、`data/observations.csv`、`data/budget_overrides.json`：本地用户数据，不入库。
- `.streamlit/secrets.toml`：真实云端凭证，不入库。

# 模拟定投决策台（Streamlit）

面向小团队/家庭试用的 DCA 定投回测与决策工具。数据层以 Google Sheets 为事实源，应用内使用“用户名 + PIN”登录（PBKDF2 加盐哈希 + 失败锁定）；未配置云端 secrets 时须显式 `DCA_AUTH_MODE=local` 才进入本地 CSV 单机模式。多用户隔离：模型缓存按用户分键、落盘缓存按用户分目录。

> 合规提示：本项目是回测/计算器工具，不构成投资建议。

## 本地运行

```bash
.venv/Scripts/streamlit run app.py
```

（本机已配 secrets 则照常登录；无凭据的机器需 `DCA_AUTH_MODE=local` 才会进单机模式，见部署指南 §3。）

全量回归完全离线，不需要云端凭据：

```bash
.venv/Scripts/python.exe -m pytest
```

备用公网入口（本机常开时）：

```bash
deploy/start-dca-tunnel.bat
```

## Streamlit Community Cloud 部署

1. 把本目录推送到 GitHub。**本仓实际是公开仓**（`Behappybehealth/sp500-nasdaq100-gold-dca`，有意设置）——公开仓 Cloud 默认就能部署；若你换成私有仓，还得在 Cloud 的 `Settings → Linked accounts → Source control` 里额外 `Authorize streamlit` 授予私有仓权限，否则部署拉不到代码。真实持仓、成交与凭据一律不入库（见 `.gitignore`），公开的只有代码、文档与策略口径。
2. 打开 https://share.streamlit.io/ → New app。
3. 选择仓库、分支，`Main file path` 填 `app.py`。
4. 在应用后台 `Settings → Secrets` 粘贴 `.streamlit/secrets.toml` 同内容（不要提交真实 secrets 到 Git）。
5. Deploy 后验证：注册/激活用户 → 写入一条测试记录 → 删除测试记录。

所需 secrets 结构见 `.streamlit/secrets.toml.example`。

## 工程文档

- **[文档门户](docs/README.md)**：全部说明文件的索引（活/冻标注、读者、更新时机）。
- [工程架构说明书](docs/ARCHITECTURE.md)：架构唯一事实源。解释技术栈、核心设计、数据流、目录职责与架构变更记录。
- [问题台账](docs/BUGLIST.md)：问题唯一事实源。每条问题按“梳理 → 1 对 1 确认 → 修复 → 验证”留痕；**未经确认不修改真实逻辑**。
- [部署与外发指南](deploy/DEPLOY.md)：只记录真实在用的 Cloud、本机和 ngrok 三条路径。

## 仓库内容说明

- `app.py`：Streamlit 主应用。
- `storage.py`：Google Sheets 优先、本地 CSV 回退的存储层。
- `scripts/dca_calculator.py`：策略/行情计算引擎。
- `tests/` + `pytest.ini`：引擎纯函数、storage 安全路径与 Streamlit 整页冒烟；只收离线虚构数据。
- `requirements.txt`：Streamlit Cloud 可安装范围；`requirements-dev.lock`：Windows/Python 3.14 开发机完整精确锁定。
- `.github/workflows/ci.yml`：push `main` 后自动跑 Windows 3.14 锁定环境与 Linux 3.12 Cloud 范围环境。
- `backtest/*.py`：可相对定位重跑的归档脚本，不作回归载体；`backtest/results*` 是 Tab5 使用的冻结产物。
- `data/config.json`：默认资产配置，需要入库。
- `data/market_history/*.csv`：行情缓存，需要入库以避免首次部署冷启动过慢。
- `data/transactions.csv`、`data/observations.csv`、`data/budget_overrides.json`：单机模式的本地用户数据，不入库；云端模式每用户落盘缓存在 `data/users/<用户>/`（同样不入库）。
- `.streamlit/secrets.toml`：真实云端凭证，不入库。

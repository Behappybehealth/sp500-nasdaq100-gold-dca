# 模拟定投决策台（Streamlit）

面向小团队/家庭试用的 DCA 定投回测与决策工具。数据层以 Google Sheets 为事实源，应用内使用“用户名 + PIN”登录（PBKDF2 加盐哈希 + 失败锁定）；未配置云端 secrets 时须显式 `DCA_AUTH_MODE=local` 才进入本地 CSV 单机模式。多用户隔离（缓存按用户分键、落盘按用户分目录）已于 2026-08-18 修复并验证，见 [BUG-001](docs/BUGLIST.md#bug-001跨用户数据串号)。

> 合规提示：本项目是回测/计算器工具，不构成投资建议。

## 本地运行

```bash
.venv/Scripts/streamlit run app.py
```

（本机已配 secrets 则照常登录；无凭据的机器需 `DCA_AUTH_MODE=local` 才会进单机模式，见部署指南 §3。）

备用公网入口（本机常开时）：

```bash
deploy/start-dca-tunnel.bat
```

## Streamlit Community Cloud 部署

1. 把本目录推送到 GitHub（建议私有仓库）。
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
- `data/config.json`：默认资产配置，需要入库。
- `data/market_history/*.csv`：行情缓存，需要入库以避免首次部署冷启动过慢。
- `data/transactions.csv`、`data/observations.csv`、`data/budget_overrides.json`：单机模式的本地用户数据，不入库；云端模式每用户落盘缓存在 `data/users/<用户>/`（同样不入库）。
- `.streamlit/secrets.toml`：真实云端凭证，不入库。

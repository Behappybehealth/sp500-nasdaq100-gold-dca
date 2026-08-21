# 文档门户

> 本项目全部说明文件的索引与可信度标注。
> **活文档**随代码同步更新、可以信赖；**冻文档**是历史快照，只增不改、不要回改。

## 活文档（最新，可信赖）

| 文件 | 读者 | 更新时机 | 一句话 |
|---|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 所有人（含非技术） | 仅顶层架构变动同期 | 顶层架构唯一事实源（概要版）：技术栈、架构图、数据流、业务链路、6 tab、目录说明 |
| [ARCHITECTURE-DETAIL.md](ARCHITECTURE-DETAIL.md) | 开发 / AI | 行为变更同期 | 架构详设：实现细节、设计动机与代价、踩坑记录、全局耦合实测 |
| [BUGLIST.md](BUGLIST.md) | 开发 / AI | 确认门四段推进 | 问题唯一事实源（每条须 1 对 1 确认后才可修） |
| [策略说明](../strategy/core-strategy.md) | 所有人 | 策略调整同期 | 三资产动态定投策略完整文档 |
| [部署与外发指南](../deploy/DEPLOY.md) | 部署者 | 部署方式变动同期 | 只写真实在用的 Cloud / 本机 / ngrok 三条路径 + §6 备份制度（Apps Script 每日快照，含部署与恢复演练指引） |
| [CLAUDE.md](../CLAUDE.md) | AI 助手 | 规矩变动同期 | AI 入口：技术栈、目录树、红线规矩 |
| [README.md](../README.md) | 访客 | 门面信息变动同期 | 项目门面 |
| [CHANGELOG.md](../CHANGELOG.md) | 所有人 | 每个 commit 同期 | 全量改动的人读版流水（每行带时刻，scripts/changelog.py 生成与校验） |
| [requirements.txt](../requirements.txt) / [requirements-dev.lock](../requirements-dev.lock) | 部署 / 开发 | 直接依赖或开发环境变动同期 | Cloud/Linux 可安装范围 + Windows/Python 3.14 开发机精确锁，两份分工不混用 |
| [pytest.ini](../pytest.ini) / [tests/](../tests/) | 开发 / CI | 行为或测试策略变动同期 | 全离线三层回归，只收 tests/，fixture 必须虚构 |
| [.github/workflows/ci.yml](../.github/workflows/ci.yml) | 开发 / 运维 | CI 触发或环境矩阵变动同期 | push main 自动验证精确开发组合与 Cloud 可安装组合 |

## 数据产物（冻，重跑才更新）

| 文件 | 说明 |
|---|---|
| [backtest/results.md](../backtest/results.md) | 5 年全样本滚动回测报告（2026-08-11 跑完） |
| [backtest/compare3.md](../backtest/compare3.md) | 三策略对比回测报告 |
| `backtest/results*.json` | Tab5 使用或留档的冻结数值产物；不得用归档脚本重跑后静默覆盖 |

`backtest/*.py` 是**归档脚本而非冻结果**：允许做不改变历史结果含义的可移植性维护（例如相对路径），可重跑但不作 pytest 回归载体；重跑前必须复制冻结 JSON，跑后比较并恢复。

## 历史快照（冻，只增不改）

| 文件 | 说明 |
|---|---|
| [plans/app-split-design.md](plans/app-split-design.md) | app.py 拆分方案（施工图纸，已执行完毕） |
| [plans/project-audit-2026-08-17.md](plans/project-audit-2026-08-17.md) | 2026-08-17 全量审计原始快照（26 条问题的出处） |
| [plans/architecture-and-p0-explained.md](plans/architecture-and-p0-explained.md) | ARCHITECTURE / BUGLIST 的前身，已被拆分取代 |
| [plans/distributed-pondering-puppy.md](plans/distributed-pondering-puppy.md) | 回测模型实施计划（已执行完） |
| [plans/proud-discovering-kitten.md](plans/proud-discovering-kitten.md) | 完整升级计划（历史） |
| [plans/toasty-yawning-dewdrop.md](plans/toasty-yawning-dewdrop.md) | Skill 改版计划（历史） |

## 规矩（与 CLAUDE.md 第 11 条互为表里）

1. 活文档头部标 `【活·更新时机：…】`；历史计划与回测结果/报告属于冻结内容。
2. 行为变更的 commit 必须同期核对上表相关活文档；冻结内容不回改。归档 `.py` 脚本允许做行为保持的维护，但不能借此改写冻结产物。

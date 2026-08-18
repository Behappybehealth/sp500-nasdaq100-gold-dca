# 文档门户

> 本项目全部说明文件的索引与可信度标注。
> **活文档**随代码同步更新、可以信赖；**冻文档**是历史快照，只增不改、不要回改。

## 活文档（最新，可信赖）

| 文件 | 读者 | 更新时机 | 一句话 |
|---|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 开发 / AI | 架构变动同期 | 架构唯一事实源：三层一边界、数据流、目录职责 |
| [BUGLIST.md](BUGLIST.md) | 开发 / AI | 确认门四段推进 | 问题唯一事实源（每条须 1 对 1 确认后才可修） |
| [策略说明](../strategy/core-strategy.md) | 所有人 | 策略调整同期 | 三资产动态定投策略完整文档 |
| [部署与外发指南](../deploy/DEPLOY.md) | 部署者 | 部署方式变动同期 | 只写真实在用的 Cloud / 本机 / ngrok 三条路径 |
| [CLAUDE.md](../CLAUDE.md) | AI 助手 | 规矩变动同期 | AI 入口：目录树、红线规矩 |
| [README.md](../README.md) | 访客 | 门面信息变动同期 | 项目门面 |
| [CHANGELOG.md](../CHANGELOG.md) | 所有人 | 每个 commit 同期 | 全量改动的人读版流水（每行带时刻，scripts/changelog.py 生成与校验） |

## 数据产物（冻，重跑才更新）

| 文件 | 说明 |
|---|---|
| [backtest/results.md](../backtest/results.md) | 5 年全样本滚动回测报告（2026-08-11 跑完） |
| [backtest/compare3.md](../backtest/compare3.md) | 三策略对比回测报告 |

## 历史快照（冻，只增不改）

| 文件 | 说明 |
|---|---|
| [plans/app-split-design.md](plans/app-split-design.md) | **半活**：app.py 拆分 6 刀方案（BUG-020 施工图纸，拆分完成前保持更新，拆完转冻） |
| [plans/project-audit-2026-08-17.md](plans/project-audit-2026-08-17.md) | 2026-08-17 全量审计原始快照（26 条问题的出处） |
| [plans/architecture-and-p0-explained.md](plans/architecture-and-p0-explained.md) | ARCHITECTURE / BUGLIST 的前身，已被拆分取代 |
| [plans/distributed-pondering-puppy.md](plans/distributed-pondering-puppy.md) | 回测模型实施计划（已执行完） |
| [plans/proud-discovering-kitten.md](plans/proud-discovering-kitten.md) | 完整升级计划（历史） |
| [plans/toasty-yawning-dewdrop.md](plans/toasty-yawning-dewdrop.md) | Skill 改版计划（历史） |

## 规矩（与 CLAUDE.md 第 11 条互为表里）

1. 活文档头部标 `【活·更新时机：…】`；冻文档标 `【冻·历史快照】`。
2. 行为变更的 commit 必须同期核对上表相关活文档；冻文档不回改。

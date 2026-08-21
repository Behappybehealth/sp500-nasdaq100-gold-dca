# -*- coding: utf-8 -*-
"""Apps Script 备份脚本（BUG-018）源码不变量回归。

全离线：只解析仓内 `deploy/backup/Code.gs` 与 `storage.py` 的源码文本/AST——
Apps Script 跑在 Google 侧，这里没有运行时；结构断言守仓内这一半，
CSV 往返与真实恢复由 DEPLOY.md §6.2 的演练覆盖（台账钉死必须真跑）。

守的是四件会让备份制度**悄悄失效**的事：
① Code.gs 的 TABLES 与 storage.py 实际读写的工作表名脱钩（加了新表忘了备份，
   或反过来备份了不存在的表，快照里静默少一份）
② 保留天数被随手改掉（30 天是拍板值，改它应该是一次有意识的决策）
③ 有人把源表 ID 硬编码进脚本（绑定脚本的意义就是零配置；硬编码一进来，
   换表格部署时脚本悄悄备份错表）
④ 恢复守卫或失败告警被"顺手简化"掉——这两条是 BUG-018 确认记录里钉死的设计
"""

from __future__ import annotations

import ast
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_GS = (_ROOT / "deploy/backup/Code.gs").read_text(encoding="utf-8")


def _storage_worksheets() -> tuple[set[str], int]:
    """storage.py 实际读写的工作表名全集 + 直接字面量调用点总数。

    两条路径都要收：`_read_ws` / `_write_ws` 的直接字面量参数（users /
    budget_overrides），以及模块级 `TABLES` 字典的键（transactions /
    observations 经 `for table in TABLES` 变量间接读写）——只收字面量会漏掉后者。
    """
    tree = ast.parse((_ROOT / "storage.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    sites = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("_read_ws", "_write_ws")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
            sites += 1
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TABLES" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            names |= {
                k.value
                for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return names, sites


def _gs_tables() -> set[str]:
    m = re.search(r"var TABLES = \[([^\]]*)\]", _GS)
    assert m, "Code.gs 里找不到 TABLES 常量，扫描失效"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_backup_tables_match_storage_worksheets():
    """备份清单 = 事实源全部工作表，两张皮一脱钩就能在这里被发现。

    最危险的备份失败形态：加了第五张表（比如审计日志）而 TABLES 没跟上，
    每日快照照常成功、邮件照常没有，直到恢复那天才发现少一张表。
    """
    storage_names, sites = _storage_worksheets()
    assert sites >= 10, f"只扫到 {sites} 个 storage 工作表调用点，扫描逻辑可能失效了"
    expected = {"users", "transactions", "observations", "budget_overrides"}
    assert storage_names == expected, (
        f"storage.py 的工作表集变了：{sorted(storage_names)}——先想清楚再动这条断言"
    )
    assert _gs_tables() == storage_names, (
        f"Code.gs TABLES={sorted(_gs_tables())} 与 storage 工作表 "
        f"{sorted(storage_names)} 不一致"
    )


def test_retention_is_30_days():
    """30 天是 BUG-018 确认记录的拍板值，不是随手可调的数字。"""
    assert re.search(r"var RETENTION_DAYS = 30;", _GS), (
        "保留天数被改动——若是有意调整，先回 BUG-018 确认记录补一笔"
    )


def test_source_sheet_is_never_hardcoded():
    """源表只允许经 getActiveSpreadsheet() 取得；openById 全脚本只许出现一次（恢复目标用）。"""
    assert "getActiveSpreadsheet" in _GS, "源表访问不见了，脚本结构被大改"
    assert _GS.count("openById(") == 1, (
        f"openById 出现 {_GS.count('openById(')} 次——源表必须走容器绑定，"
        "硬编码 ID 会让换表格部署时悄悄备份错表"
    )
    assert not re.search(r"[A-Za-z0-9_-]{25,}", _GS), (
        "Code.gs 里出现疑似硬编码的长 ID 串"
    )


def test_restore_guard_and_failure_alert():
    """两条钉死的设计：恢复拒绝源表自身；dailyBackup 失败必须发邮件。"""
    assert "拒绝恢复到源表自身" in _GS, "恢复守卫被移除——生产表可能被手滑覆写"
    assert "targetSpreadsheetId === src.getId()" in _GS, "守卫比较逻辑不在预期位置"
    assert "MailApp.sendEmail" in _GS and "[DCA] 每日备份失败" in _GS, (
        "失败邮件告警被移除——每日任务静默失败等于回到没备份"
    )


def test_deploy_doc_covers_trigger_and_drill():
    """DEPLOY.md 必须覆盖：建触发器、恢复演练、快照落点——少一项部署就断链。"""
    doc = (_ROOT / "deploy/DEPLOY.md").read_text(encoding="utf-8")
    for needle in ("dailyBackup", "restoreSnapshot", "触发器", "恢复演练", "dca-backups"):
        assert needle in doc, f"DEPLOY.md 缺少 {needle!r} 的部署/演练说明"

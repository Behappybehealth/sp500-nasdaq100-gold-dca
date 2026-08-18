# -*- coding: utf-8 -*-
"""CHANGELOG.md 维护工具（CLAUDE.md 第 12 条的配套脚本）。

用法：
    python scripts/changelog.py add <hash>     从 git 取提交时刻，生成 CHANGELOG 行草稿供粘贴
    python scripts/changelog.py --check        校验每个 commit 在 CHANGELOG.md 都有行且时刻正确

行格式约定（人可读、脚本可校验）：
    - HH:MM:SS [类型] 一句话（`短hash`；BUG-0XX/0YY）
  - 行首时刻 = 行内第一个反引号短 hash 的 commit 时刻（本地时区，取自 git，非手写）
  - 一行多 hash 时，时刻只对第一个 hash 负责，其余 hash --check 只验存在
  - 尾随约定：commit 自身的那一行由**下一个** commit 携带入库（hash 提交后才存在）
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

TYPE_MAP = {
    "fix": "修复",
    "feat": "功能",
    "refactor": "重构",
    "docs": "文档",
    "chore": "杂务",
    "data": "数据",
    "perf": "性能",
    "test": "测试",
}


def _git(*args) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def _short(hash_or_ref: str) -> str:
    return _git("rev-parse", "--short=7", hash_or_ref).strip()


def _time_of(short_hash: str) -> str:
    return _git("show", "-s", "--format=%cd", "--date=format-local:%H:%M:%S", short_hash).strip()


def cmd_add(ref: str) -> int:
    short = _short(ref)
    t = _time_of(short)
    subject = _git("show", "-s", "--format=%s", short).strip()
    m = re.match(r"^(\w+)(\([^)]*\))?:\s*(.*)$", subject)
    if m and m.group(1) in TYPE_MAP:
        label, body = TYPE_MAP[m.group(1)], m.group(3)
    else:
        label, body = "杂务", subject
    # BUG 引用从主题尾部括号挪进 hash 括号，并归一 BUG-026/BUG-021 → BUG-026/021
    bugs = ""
    bm = re.search(r"（(BUG-[\d/、BUG\s-]+)）\s*$", body)
    if bm:
        raw = re.sub(r"BUG-(\d+)/BUG-", r"\1/", bm.group(1))
        bugs = "；" + re.sub(r"\s+", "", raw)
        body = body[: bm.start()].rstrip()
    print(f"- {t} [{label}] {body}（`{short}`{bugs}）")
    return 0


def cmd_check() -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    log_lines = _git("log", "--format=%h %cd", "--date=format-local:%H:%M:%S").splitlines()
    commits = [ln.split(" ", 1) for ln in log_lines if ln.strip()]

    # CHANGELOG 逐行解析：行首时刻 + 行内全部反引号短 hash
    line_re = re.compile(r"^-\s+(?:(\d{2}:\d{2}:\d{2})\s+)?\[")
    hash_re = re.compile(r"`([0-9a-f]{7,})`")
    entry = {}  # short_hash -> (line_no, line_time, is_first_hash_of_line)
    for i, ln in enumerate(text.splitlines(), 1):
        lm = line_re.match(ln)
        if not lm:
            continue
        hashes = hash_re.findall(ln)
        for j, h in enumerate(hashes):
            entry.setdefault(h, (i, lm.group(1), j == 0))

    problems = []
    for short, t in commits:
        hit = entry.get(short)
        if hit is None:
            problems.append(f"MISSING  {short} {t}  CHANGELOG 无此 commit 的行")
            continue
        line_no, line_time, is_first = hit
        if is_first:
            if line_time is None:
                problems.append(f"NO_TIME  {short} {t}  第 {line_no} 行缺行首时刻")
            elif line_time != t:
                problems.append(
                    f"TIME_BAD {short}  git={t}  CHANGELOG 第 {line_no} 行={line_time}"
                )

    if problems:
        print(f"[NG] {len(problems)} 处问题：")
        for p in problems:
            print("  " + p)
        return 1
    print(f"[OK] {len(commits)} 个 commit 全部有行，时刻与 git 一致")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "add":
        sys.exit(cmd_add(sys.argv[2]))
    if len(sys.argv) == 2 and sys.argv[1] == "--check":
        sys.exit(cmd_check())
    print(__doc__)
    sys.exit(2)

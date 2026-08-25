#!/usr/bin/env python3
"""把 pytest 的 junit XML 汇总成一段 Markdown，供 CI 写进 GitHub 运行页的 Summary。

为什么要它：CI 的结论默认只体现在"绿勾/红叉 + 一大段日志"里，谁在什么版本上跑了
多少用例、跪在哪条上，都得展开日志翻。本脚本把关键数字提到运行页顶部（GitHub 的
`$GITHUB_STEP_SUMMARY`），失败时连带列出失败用例名，点开就能看。

用法（CI 里）：
    python .github/scripts/junit_summary.py reports/pytest-3.12.xml >> "$GITHUB_STEP_SUMMARY"

本地也能直接跑（先 `pytest -q --junitxml=reports/pytest-local.xml`），输出就是
CI 上会看到的那段文字。

设计约束：只用标准库（CI 里这一步不装任何额外依赖）；**自身绝不失败**——它是
报告环节，不是门禁。XML 缺失/损坏时打印醒目警告并以 0 退出，绝不能因为"报告没生成"
把一次本来绿的 CI 弄红（真正的判定权在 pytest 那一步）。注意 CI 里这一步是
`python ... >> "$GITHUB_STEP_SUMMARY"`，脚本非零退出**会**让那个 step 失败进而拖红
整个 job，所以末尾还兜了一层 except。

警告必须**醒目**（GitHub 的 `> [!WARNING]` / `> [!CAUTION]` 提示块）：本脚本是
"结论留在 GitHub 上"这条规矩的唯一执行者，它坏掉时 job 依然绿——失败如果只是
一行小字，证据链断了都没人发现。
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _warn(label: str, detail: str) -> None:
    print(f"> [!WARNING]\n> **测试报告 `{label}` 汇总失败**：{detail}\n>\n"
          f"> 本步骤不参与门禁（job 仍绿），测试结论请以 Pytest 步骤的日志为准。")


def _testsuite(path: Path) -> ET.Element | None:
    root = ET.parse(path).getroot()
    if root.tag == "testsuite":
        return root
    return root.find("testsuite")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：junit_summary.py <junit.xml>", file=sys.stderr)
        return 0

    path = Path(argv[1])
    label = path.stem.removeprefix("pytest-") or path.stem

    if not path.is_file():
        _warn(label, f"未找到 `{path}`——pytest 可能在生成报告前就中断了。")
        return 0

    try:
        suite = _testsuite(path)
    except ET.ParseError as exc:
        _warn(label, f"报告文件解析失败（{exc}）。")
        return 0

    if suite is None:
        _warn(label, "报告里没有 testsuite 节点。")
        return 0

    total = int(suite.get("tests", "0"))
    failures = int(suite.get("failures", "0"))
    errors = int(suite.get("errors", "0"))
    skipped = int(suite.get("skipped", "0"))
    seconds = float(suite.get("time", "0") or 0)
    passed = total - failures - errors - skipped
    ok = failures == 0 and errors == 0

    print(f"### {'✅' if ok else '❌'} 测试报告 `{label}`\n")
    print("| 通过 | 失败 | 错误 | 跳过 | 合计 | 耗时 |")
    print("|---:|---:|---:|---:|---:|---:|")
    print(f"| {passed} | {failures} | {errors} | {skipped} | {total} | {seconds:.1f}s |")

    if not ok:
        print("\n**没过的用例：**\n")
        for case in suite.iter("testcase"):
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            if bad is None:
                continue
            name = f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":")
            first_line = (bad.get("message") or "").strip().splitlines()
            hint = first_line[0][:160] if first_line else ""
            print(f"- `{name}`{f' — {hint}' if hint else ''}")

    return 0


def run(argv: list[str]) -> int:
    try:
        return main(argv)
    except Exception as exc:  # noqa: BLE001
        print(f"> [!CAUTION]\n> **`junit_summary.py` 自身故障**（`{type(exc).__name__}: {exc}`）"
              f"——测试结论未汇总到本页。\n>\n"
              f"> 本步骤不参与门禁（job 仍绿），请以 Pytest 步骤的日志为准，"
              f"并尽快修复本脚本。")
        return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv))

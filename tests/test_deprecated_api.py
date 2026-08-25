"""弃用 API 零残留回归（BUG-035）：`use_container_width` 不许回流 + 下界不许回落。

全离线、零 I/O：只解析源码 AST 与 requirements.txt，不起 Streamlit、不碰网络。

为什么值得一条测试：`use_container_width` 在 2025-12-31 后进入删除窗口，
Cloud 每次唤醒都按 requirements.txt 重新解析依赖——下界一旦回落到没有
`width=` 参数的旧版，或者有人照抄网络旧示例把这个参数写回来，线上就是
启动即 TypeError，而且登录页最先死。两条断言各守一头：源码端零残留、
安装端下界托底。
"""

from __future__ import annotations

import ast
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _py_files() -> list[pathlib.Path]:
    """业务代码全集：入口 + src/ 下所有模块（引擎与脚本不用 Streamlit UI）。"""
    return [_ROOT / "app.py", *sorted((_ROOT / "src").rglob("*.py"))]


def _keyword_calls(arg_name: str) -> list[tuple[str, int, str]]:
    """全部带 `arg_name=` 关键字实参的调用点，连同实参源码一起交出。"""
    out: list[tuple[str, int, str]] = []
    for f in _py_files():
        rel = f.relative_to(_ROOT).as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == arg_name:
                    out.append((rel, node.lineno, ast.unparse(kw.value)))
    return out


def test_no_use_container_width_anywhere():
    """`use_container_width` 一处都不许剩、也不许新增。

    等价替换是 `width="stretch"`（原 True）/ `width="content"`（原 False）。
    扫描逻辑防呆：替换成 `width="stretch"` 的调用点必须 >= 20，
    少于这个数说明扫描失效或有人把布局参数删了而不是换名。
    """
    hits = _keyword_calls("use_container_width")
    assert not hits, "use_container_width 已过删除窗口，必须换成 width=：\n" + "\n".join(
        f"{rel}:{lineno} use_container_width={val}" for rel, lineno, val in hits
    )
    stretch = [s for s in _keyword_calls("width") if s[2] == "'stretch'"]
    assert len(stretch) >= 20, (
        f"只扫到 {len(stretch)} 处 width=\"stretch\"，扫描逻辑可能失效了"
    )


def test_streamlit_lower_bound_covers_width_param():
    """requirements.txt 的 streamlit 下界必须 >= 1.61.1。

    `width=` 布局参数 1.46+ 才有；下界低于它，Cloud 解析到旧版时
    全部布局调用 TypeError，等于把 BUG-035 的引信又接回去。
    """
    text = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
    m = re.search(r"^streamlit>=(\d+)\.(\d+)\.(\d+)", text, re.MULTILINE)
    assert m, "requirements.txt 里找不到 streamlit 的下界声明"
    ver = tuple(int(g) for g in m.groups())
    assert ver >= (1, 61, 1), (
        f"streamlit 下界 {'.'.join(m.groups())} 低于 1.61.1，width= 参数不存在"
    )

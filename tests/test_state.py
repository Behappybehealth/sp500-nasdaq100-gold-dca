# -*- coding: utf-8 -*-
"""session_state 键登记表（BUG-032）回归：常量化守得住 + 登记表不漂移。

全离线、零 I/O：只解析源码 AST，不起 Streamlit、不碰网络。

这里测的不是"常量能用"——那太便宜了。测的是四件会让 A 档**慢慢失效**的事：
① 有人图省事又写回裸字面量（拼错就静默建新键，这正是 BUG-032 的病根）
② 登记表与实际用键脱钩（新键绕过 state.py，或键没了常量还留着当死代码）
③ 业务键与 widget key 撞名（两者共享同一个 dict 命名空间，撞了就互相覆盖）
④ `synced` 的失效化又散回各处（跨模块协议重新变成隐式约定）
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src import state

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_STATE_FILE = "src/state.py"


def _py_files() -> list[pathlib.Path]:
    """业务代码全集：入口 + src/ 下所有模块（测试与引擎不碰 session_state）。"""
    return [_ROOT / "app.py", *sorted((_ROOT / "src").rglob("*.py"))]


def _is_session_state(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "session_state"
        and isinstance(node.value, ast.Name)
        and node.value.id == "st"
    )


def _key_sites() -> list[tuple[str, int, ast.AST]]:
    """全部 `st.session_state` 取键点，连同"键是什么表达式"一起交出。

    四种写法都要收：下标 `[k]`、方法 `.get/.pop/.setdefault(k`、成员判断 `k in ...`。
    只认前三种会漏掉 `K_USER not in st.session_state` 这条门闸判据。
    """
    sites: list[tuple[str, int, ast.AST]] = []
    for f in _py_files():
        rel = f.relative_to(_ROOT).as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            key: ast.AST | None = None
            if isinstance(node, ast.Subscript) and _is_session_state(node.value):
                key = node.slice
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _is_session_state(node.func.value)
                and node.args
            ):
                key = node.args[0]
            elif isinstance(node, ast.Compare) and any(
                _is_session_state(c) for c in node.comparators
            ):
                key = node.left
            if key is not None:
                sites.append((rel, node.lineno, key))
    return sites


def _state_imports(path: pathlib.Path) -> set[str]:
    """某文件从 `src.state` 导入了哪些名字（用来分辨"真常量"与"同名土制变量"）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "state":
            names |= {a.asname or a.name for a in node.names}
    return names


# ============================================================
# 1. 不许写回裸字面量
# ============================================================

def test_no_bare_session_state_key_literals():
    """业务代码里不得再出现裸键字面量。

    这条是 BUG-032 的核心约束：`session_state` 写任意键都合法，
    `synced` 手滑写成 `synched` 不会报错，只会让"是否已同步"永久判错，
    而建议基于陈旧缓存出错时没有任何提示。常量引用把它提前成 import 期报错。
    """
    sites = _key_sites()
    assert len(sites) >= 40, f"只扫到 {len(sites)} 个取键点，扫描逻辑可能失效了"
    bare = [
        f"{rel}:{lineno} 用了裸字面量 {ast.unparse(key)}"
        for rel, lineno, key in sites
        if isinstance(key, ast.Constant)
    ]
    assert not bare, "session_state 键必须走 src/state.py 的常量：\n" + "\n".join(bare)


# ============================================================
# 2. 登记表与实际用键互相覆盖
# ============================================================

def test_every_key_expression_is_a_registered_constant():
    """每个取键点用的名字，都必须是本文件从 `src.state` 导进来的常量。

    只断言"不是字面量"不够——一个文件自己写 `K_USER = "usr"` 也能骗过那条，
    而这正好是最难查的一类错：名字看着对，值是错的。
    """
    bad = []
    imports = {f.relative_to(_ROOT).as_posix(): _state_imports(f) for f in _py_files()}
    for rel, lineno, key in _key_sites():
        if rel == _STATE_FILE:
            continue  # 定义处自己不 import 自己（invalidate_sync 直接用 K_SYNCED）
        if not isinstance(key, ast.Name):
            bad.append(f"{rel}:{lineno} 键不是常量名：{ast.unparse(key)}")
        elif key.id not in imports[rel]:
            bad.append(f"{rel}:{lineno} 的 {key.id} 不是从 src.state 导入的")
    assert not bad, "\n".join(bad)


def test_registry_and_usage_cover_each_other():
    """`ALL_KEYS` 既不许漏（有键没登记），也不许多（常量已成死代码）。

    登记表的价值全在"它就是事实"。一旦它开始漂移，读它的人会做出错误推断——
    比如以为某个键已经没人用了，动手删掉，而实际还活着。
    """
    used_names = {key.id for _, _, key in _key_sites() if isinstance(key, ast.Name)}
    used_values = {getattr(state, n) for n in used_names}
    assert used_values <= state.ALL_KEYS, (
        f"这些键在用但没登记进 ALL_KEYS：{sorted(used_values - state.ALL_KEYS)}"
    )
    assert state.ALL_KEYS <= used_values, (
        f"这些键登记了但已无人使用（死常量）：{sorted(state.ALL_KEYS - used_values)}"
    )


# ============================================================
# 3. 不与 widget key 撞名
# ============================================================

def _widget_keys() -> list[tuple[str, int, str]]:
    """全部 widget 的 `key="..."`。

    必须扫任意调用而不是只扫 `st.xxx(...)`：本项目大量控件挂在列上
    （`d2.button(..., key="btn_dup_cancel")`），只认 `st.` 前缀会漏掉它们。
    """
    out = []
    for f in _py_files():
        rel = f.relative_to(_ROOT).as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "key" and isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, str
                ):
                    out.append((rel, node.lineno, kw.value.value))
    return out


def test_business_keys_do_not_collide_with_widget_keys():
    """widget key 与业务键住在同一个 dict 里，撞名就是互相覆盖。

    Streamlit 会把 `key="amt_base"` 的控件值直接写进 `st.session_state["amt_base"]`。
    要是哪天有人给控件起了个业务键的名字，控件值会把业务状态踩掉，
    表现为"输入框一动，登录态就乱"——极难从现象倒推到原因。
    """
    widgets = _widget_keys()
    assert len(widgets) >= 5, f"只扫到 {len(widgets)} 个 widget key，扫描逻辑可能失效了"
    clash = [f"{rel}:{lineno} 的 key={name!r}" for rel, lineno, name in widgets if name in state.ALL_KEYS]
    assert not clash, "widget key 与业务键撞名：\n" + "\n".join(clash)


# ============================================================
# 4. synced 的失效化只有一个入口
# ============================================================

def test_sync_invalidation_is_centralised():
    """`K_SYNCED` 的 `pop` 只许出现在 `src/state.py`。

    这是 BUG-032 ② 的那条隐式协议：置位在认证链、想触发重同步的人在侧栏。
    散着写的时候，第三处想触发重同步得先知道"pop 掉 synced"这个约定存在，
    而它没写在任何地方。收敛成 `invalidate_sync()` 之后，它是个能 grep 到的名字。
    """
    offenders = []
    for f in _py_files():
        rel = f.relative_to(_ROOT).as_posix()
        if rel == _STATE_FILE:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pop"
                and _is_session_state(node.func.value)
                and node.args
                and ast.unparse(node.args[0]) in ("K_SYNCED", repr(state.K_SYNCED))
            ):
                offenders.append(f"{rel}:{node.lineno} 直接 pop 了同步标记")
    assert not offenders, "失效化必须走 state.invalidate_sync()：\n" + "\n".join(offenders)

    sidebar = (_ROOT / "src/ui/sidebar.py").read_text(encoding="utf-8")
    assert "invalidate_sync()" in sidebar, "侧栏 🔄 刷新必须经 invalidate_sync() 触发重同步"


def test_invalidate_sync_pops_the_flag(monkeypatch):
    """行为断言：调用后标记必须真的没了（且键不存在时不炸）。"""
    fake: dict = {state.K_SYNCED: True, state.K_USER: "虚构用户"}
    monkeypatch.setattr(state.st, "session_state", fake)
    state.invalidate_sync()
    assert state.K_SYNCED not in fake, "同步标记没被清掉，下一趟不会重新同步"
    assert fake[state.K_USER] == "虚构用户", "只该动同步标记，不该碰别的键"
    state.invalidate_sync()  # 键已不存在时也不得抛


@pytest.mark.parametrize("const", sorted(state.ALL_KEYS))
def test_key_values_are_non_empty_strings(const):
    """键必须是非空字符串——空串是合法 dict 键，但会让所有空串键互相覆盖。"""
    assert isinstance(const, str) and const.strip()

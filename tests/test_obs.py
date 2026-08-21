# -*- coding: utf-8 -*-
"""运行日志（BUG-017）回归：配置的三条不变量 + 失败留痕 + 不泄密。

全离线：只用 tmp_path 和假连接，一次网络都不发（`conftest._deny_network` 兜底）。

这里测的不是"日志能打出来"——那太便宜了。测的是三件会**静默毁掉日志价值**的事：
① 幂等（Streamlit 整脚本重跑，不去重则日志行随交互次数翻倍）
② 轮转（BUG-017 原文的另一半就是"日志无上限写满磁盘"，不轮转等于把它请回来）
③ 异常原文进日志（`except` 里丢掉 `e` 是 BUG-017 的真正痛点）
外加一条安全断言：日志调用点绝不引用 PIN / 哈希 / 盐。
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

import storage
from src import obs

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_dca_logger():
    """`logging` 的 logger 是全进程单例——用完必须还原，否则测试之间互相污染。

    `propagate` 也要还原：`setup_logging` 会把它置 False，而 pytest 的 `caplog`
    依赖向 root 冒泡，留着 False 会让后面的用例莫名收不到日志。
    """
    lg = logging.getLogger("dca")
    saved_handlers, saved_level, saved_prop = lg.handlers[:], lg.level, lg.propagate
    lg.handlers = []
    yield lg
    for h in lg.handlers:
        h.close()
    lg.handlers, lg.level, lg.propagate = saved_handlers, saved_level, saved_prop


class _Capture(logging.Handler):
    """直接挂在 `dca` 上收记录——不能用 caplog，因为 setup_logging 关掉了向 root 的冒泡。"""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


# ============================================================
# 1. 配置本身的三条不变量
# ============================================================

def test_setup_logging_is_idempotent(clean_dca_logger, tmp_path):
    """重复调用不得追加 handler。

    Streamlit 每次交互把整个脚本从头重跑，app.py 里的 setup_logging 会被反复执行。
    不幂等的后果不是报错，而是**同一条日志打 N 遍**，N 随本次会话的交互次数增长——
    这种问题在日志里看起来像"系统疯了"，实际只是配置写漏了一行。
    """
    obs.setup_logging(tmp_path)
    first = len(clean_dca_logger.handlers)
    for _ in range(5):
        obs.setup_logging(tmp_path)
    assert len(clean_dca_logger.handlers) == first


def test_file_handler_rotates(clean_dca_logger, tmp_path):
    """文件 handler 必须带轮转上限——这是 BUG-017 的另一半，不能修一半请回来一半。"""
    obs.setup_logging(tmp_path)
    files = [
        h
        for h in clean_dca_logger.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore[attr-defined]
    ]
    assert len(files) == 1, "应恰好有一个带轮转的文件 handler"
    assert files[0].maxBytes > 0, "maxBytes=0 等于不轮转，日志会无上限写满磁盘"
    assert files[0].backupCount > 0, "backupCount=0 等于轮转后直接丢，留不下历史"


def test_does_not_hijack_root_logger(clean_dca_logger, tmp_path):
    """只配 `dca` 子树，不碰 root。

    碰 root 有两个后果：gspread / urllib3 / streamlit 的噪声全涌进来，
    以及 Streamlit 自己的 handler 会让每行日志重复一遍。
    """
    root_before = logging.getLogger().handlers[:]
    obs.setup_logging(tmp_path)
    assert logging.getLogger().handlers == root_before
    assert clean_dca_logger.propagate is False


# ============================================================
# 2. 两个落点
# ============================================================

def test_writes_to_both_stderr_and_file(clean_dca_logger, tmp_path, capsys):
    """stderr 与 logs/dca.log 都要有——Cloud 只收 stderr，本机长期部署只留文件，缺一不可。"""
    obs.setup_logging(tmp_path)
    logging.getLogger("dca.storage").error("probe_event table=%s", "虚构表")

    assert "probe_event table=虚构表" in capsys.readouterr().err
    body = (tmp_path / "dca.log").read_text(encoding="utf-8")
    assert "probe_event table=虚构表" in body
    assert "dca.storage" in body, "行内要带频道名，否则分不清是谁记的"
    assert "ERROR" in body


def test_unwritable_log_dir_degrades_but_keeps_stderr(clean_dca_logger, tmp_path, monkeypatch):
    """落盘不可用不能阻断应用：文件那路没了，stderr 那路必须还在。"""
    def boom(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", boom)
    obs.setup_logging(tmp_path / "nope")  # 不抛
    kinds = {type(h).__name__ for h in clean_dca_logger.handlers}
    assert "StreamHandler" in kinds
    assert "RotatingFileHandler" not in kinds


# ============================================================
# 3. 失败留痕（BUG-017 ⑤ 的验证条款）
# ============================================================

def test_storage_read_failure_logs_original_exception(clean_dca_logger, monkeypatch):
    """读失败必须把**原始异常文本**记下来。

    这是 BUG-017 的核心价值：`except` 把 `e` 丢掉之后，
    "凭据过期""配额撞满""网络抖动"在事后完全无法区分，而三者处置方式不同。
    """
    cap = _Capture()
    cap.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    clean_dca_logger.addHandler(cap)
    clean_dca_logger.setLevel(logging.INFO)

    monkeypatch.setattr(storage, "sheets_enabled", lambda: True)
    storage._SHEET_CACHE.clear()

    class _Conn:
        def read(self, *a, **kw):
            raise RuntimeError("quota exceeded for this minute")

    monkeypatch.setattr(storage, "_conn", lambda: _Conn())
    with pytest.raises(storage.SheetReadError):
        storage._read_ws("transactions", storage.TX_FIELDS)
    storage._SHEET_CACHE.clear()

    assert len(cap.lines) == 1
    line = cap.lines[0]
    assert "ERROR dca.storage sheet_read_failed" in line
    assert "table=transactions" in line
    assert "quota exceeded for this minute" in line, "原始异常文本丢了，日志就只剩一句无信息量的失败"


# ============================================================
# 4. 不泄密（PIN / 哈希 / 盐一律不进日志）
# ============================================================

_FORBIDDEN = {"pin", "pin2", "pin_hash", "salt"}


def _log_call_sources() -> list[tuple[str, int, str]]:
    """抓出全项目每一个 `_log.xxx(...)` 调用点的源码文本。

    用 ast 而不是正则：日志调用大量跨行，正则数不清括号。
    """
    files = [_ROOT / "storage.py", *sorted((_ROOT / "src").rglob("*.py"))]
    found = []
    for f in files:
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_log"
            ):
                seg = ast.get_source_segment(src, node) or ""
                found.append((f.relative_to(_ROOT).as_posix(), node.lineno, seg))
    return found


def test_log_calls_never_reference_secrets():
    """日志调用点不得引用 PIN / 哈希 / 盐。

    认证路径的日志天然离这些变量只有一行之隔（`_auth["pin"]` 就在同一个 dict 里），
    一次手滑就会把明文 PIN 写进 logs/dca.log 和 Cloud 日志面板，且**没有任何报错**。
    所以这条不靠人工 review，靠断言。
    注意用词边界：事件名 `set_pin_error` 里的 `pin` 两侧都是下划线，不算命中。
    """
    calls = _log_call_sources()
    assert len(calls) >= 10, f"只扫到 {len(calls)} 个日志调用点，扫描逻辑可能失效了"
    bad = []
    for path, lineno, seg in calls:
        names = {
            n.id if isinstance(n, ast.Name) else ""
            for n in ast.walk(ast.parse(seg.strip()))
            if isinstance(n, ast.Name)
        }
        consts = {
            n.value
            for n in ast.walk(ast.parse(seg.strip()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        hit = (names | consts) & _FORBIDDEN
        if hit:
            bad.append(f"{path}:{lineno} 引用了 {sorted(hit)}")
    assert not bad, "日志调用点触到机密：\n" + "\n".join(bad)

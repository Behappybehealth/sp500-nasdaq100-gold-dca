"""共享 fixture 与 sys.path 接线。

引擎是独立脚本（`scripts/dca_calculator.py`，不是包），所以要把 `scripts/` 与仓库根
都塞进 `sys.path` 才能 import。全部测试**必须离线**——CI 里联网抓行情会被限流，
那种红是噪音不是信号（见 BUG-015 确认记录）。
"""

from __future__ import annotations

import logging
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for p in (REPO, REPO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ============================================================
# 拒网守卫：离线是硬约束，不能只靠每个用例各自 patch
# ============================================================

# 回环放行的原因：asyncio 在 Windows 上用 socketpair() 自管道，连它都拦会把
# Streamlit 的 AppTest 一并打死——那是自伤，不是防护。
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


class NetworkUseInTests(RuntimeError):
    """测试里出现真实网络调用。这是被测代码或夹具的缺陷，不是环境问题。"""


def _host_of(addr) -> str:
    if isinstance(addr, (tuple, list)) and addr:
        return str(addr[0])
    return str(addr)


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch):
    """所有用例默认断网，漏 patch 的抓价路径当场炸而不是静默出网。

    逐个出网口都堵上，因为本项目有四条互不相干的出网路径：引擎 `urllib`
    直连 Yahoo Chart、yfinance 兜底、`curl` 子进程抓东方财富、gspread 连
    Google Sheets。只 patch 其中一两个函数，新增或改名的调用点就会绕过守卫，
    在 CI 里变成限流噪音，或更糟——误写生产表。
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection

    def _blocked(what: str, target: object) -> NetworkUseInTests:
        return NetworkUseInTests(
            f"测试试图联网：{what}({target!r})。测试必须离线——"
            f"请把对应抓取函数 monkeypatch 成虚构数据（见 BUG-015 确认记录）。"
        )

    def connect(self, addr, *a, **kw):
        if _host_of(addr) not in _LOOPBACK:
            raise _blocked("socket.connect", addr)
        return real_connect(self, addr, *a, **kw)

    def connect_ex(self, addr, *a, **kw):
        if _host_of(addr) not in _LOOPBACK:
            raise _blocked("socket.connect_ex", addr)
        return real_connect_ex(self, addr, *a, **kw)

    def getaddrinfo(host, *a, **kw):
        if str(host) not in _LOOPBACK:
            raise _blocked("socket.getaddrinfo", host)
        return real_getaddrinfo(host, *a, **kw)

    def create_connection(addr, *a, **kw):
        if _host_of(addr) not in _LOOPBACK:
            raise _blocked("socket.create_connection", addr)
        return real_create_connection(addr, *a, **kw)

    def popen(self, *a, **kw):
        raise _blocked("subprocess", (a[:1] or kw.get("args")))

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    # 子进程一并堵死：引擎 subprocess 与 curl 抓价在测试里都该是 patch 掉的。
    monkeypatch.setattr(subprocess.Popen, "__init__", popen)


# ============================================================
# 日志隔离：测试不得写进运行时日志文件
# ============================================================

@pytest.fixture(autouse=True)
def _quarantine_logging():
    """预占 `dca` logger 的 handler 槽位，让 app.py 的 setup_logging 变成空操作。

    不隔离的后果是真实的：AppTest 会执行 `app.py`，其中
    `setup_logging(CODE_DIR / "logs")` 指向**工作树里的** `logs/dca.log`，
    于是 `test_storage.py` 造的假异常（"network down"、"boom"）会和线上真故障
    混在同一个文件里——排查时分不清哪行是真的，日志的价值就废了。

    手法是复用 `setup_logging` 自己的幂等守卫（handler 非空即返回），
    而不是去 patch 它——被测行为因此保持原样。`test_obs.py` 需要真配置时，
    用它自己的 `clean_dca_logger` 清空槽位即可拿回真实行为。
    """
    lg = logging.getLogger("dca")
    saved, saved_prop = lg.handlers[:], lg.propagate
    lg.handlers = [logging.NullHandler()]
    yield
    lg.handlers, lg.propagate = saved, saved_prop


@pytest.fixture
def assets() -> dict:
    """三资产定义，形状与 data/config.json 的 assets 段一致（数值即当前实装）。"""
    return {
        "sp500": {"name_cn": "标普500", "symbol": "SPY", "index_symbol": "^GSPC",
                  "neutral_weight": 0.35, "min_weight": 0.2, "max_weight": 0.55},
        "nasdaq100": {"name_cn": "纳指100", "symbol": "QQQ", "index_symbol": "^NDX",
                      "neutral_weight": 0.45, "min_weight": 0.3, "max_weight": 0.7},
        "gold": {"name_cn": "黄金", "symbol": "XAUT", "index_symbol": "GC=F",
                 "neutral_weight": 0.2, "min_weight": 0.1, "max_weight": 0.3},
    }


@pytest.fixture
def model() -> dict:
    """模型参数，与 data/config.json 的 model 段同形。"""
    return {
        "base_amount_rmb": 5000,
        "deploy_gain": 1.1,
        "deploy_max": 1.8,
        "skip_below": 0.15,
        "score_weights": {"value": 0.50, "trend": 0.25, "momentum": 0.15,
                          "heat": 0.45, "heat_quad": 0.20, "volatility": 0.15},
        "tilt_strength": 0.9,
        "defense_boost": 0.6,
    }

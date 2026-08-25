"""拒网守卫的自测：守卫本身也得被罩住。

守卫（`conftest._deny_network`）是「测试必须离线」这条硬约束的唯一执行者。
它一旦被改坏或退化成空壳，套件会继续全绿，而 CI 里悄悄开始抓 Yahoo——
那种红是限流噪音，排查起来最费时。所以把它的四个出网口和回环放行都钉死在用例里：
守卫失效 → 这个文件先红。
"""

from __future__ import annotations

import socket
import subprocess
import urllib.error
import urllib.request

import pytest
from conftest import NetworkUseInTests


def test_urllib_直连被拦():
    """引擎抓 Yahoo Chart 走的就是 urllib，这是最主要的出网口。"""
    with pytest.raises(NetworkUseInTests):
        urllib.request.urlopen("https://query1.finance.yahoo.com/v8/finance/chart/SPY", timeout=1)


def test_dns_解析被拦():
    """拦在 getaddrinfo 而非 connect，是为了连 DNS 查询都不发出去。"""
    with pytest.raises(NetworkUseInTests):
        socket.getaddrinfo("query1.finance.yahoo.com", 443)


def test_裸_socket_连接被拦():
    with pytest.raises(NetworkUseInTests):
        socket.socket().connect(("1.1.1.1", 443))


def test_子进程被拦():
    """XAU/BTC 实时价走 curl 子进程，引擎本身也是 subprocess 调起来的。"""
    with pytest.raises(NetworkUseInTests):
        subprocess.Popen(["curl", "-s", "https://push2.eastmoney.com/api/qt/stock/get"])


def test_create_connection_出网被拦():
    with pytest.raises(NetworkUseInTests):
        socket.create_connection(("query1.finance.yahoo.com", 443), timeout=1)


def test_create_connection_回环仍能真连():
    """守卫的回环分支必须真的把调用转给原函数，不能只是"不抛异常"。"""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        with socket.create_connection(server.getsockname(), timeout=2) as client:
            conn, _ = server.accept()
            conn.close()
            assert client.fileno() != -1
    finally:
        server.close()


def test_回环仍放行():
    """asyncio 在 Windows 上靠 socketpair 自管道，拦了它 AppTest 会一起死。"""
    assert socket.getaddrinfo("127.0.0.1", 0)
    a, b = socket.socketpair()
    a.close()
    b.close()


def test_守卫不吞正常异常():
    """守卫抛的是 NetworkUseInTests，别让它跟真实网络错误混成一类。"""
    assert issubclass(NetworkUseInTests, RuntimeError)
    assert not issubclass(NetworkUseInTests, urllib.error.URLError)

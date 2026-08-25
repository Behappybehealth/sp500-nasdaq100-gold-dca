"""双实现一致性回归：子进程隔离导致同规则代码有两份，必须有机制保证同步。

`biz_today()` 在 `src/dates.py` 和 `scripts/dca_types.py` 各一份——子进程隔离的代价。
注释说"两处必须同改"，但没有测试保证。这个文件就是那条保证：有人改了一处忘了
另一处，这里先红。

全离线、零 I/O：只调纯函数 + monkeypatch 固定时刻。
"""

from __future__ import annotations

from datetime import datetime, timezone

import dca_types
import pytest
from src import dates as web_dates


def test_biz_today_returns_same_date():
    """两处 biz_today() 同一时刻调用必须返回同一日期——逻辑漂移的兜底。"""
    assert web_dates.biz_today() == dca_types.biz_today()


@pytest.mark.parametrize(
    "utc_iso, expected_beijing_date",
    [
        # 午夜边界：UTC 16:00 = 北京时间次日 00:00——biz_today() 存在的全部理由
        ("2026-08-25T16:00:00+00:00", "2026-08-26"),
        # 午夜前一刻：UTC 15:59 = 北京时间 23:59——仍是"今天"
        ("2026-08-25T15:59:00+00:00", "2026-08-25"),
        # 正午：UTC 04:00 = 北京时间 12:00
        ("2026-08-25T04:00:00+00:00", "2026-08-25"),
    ],
)
def test_biz_today_midnight_boundary(utc_iso, expected_beijing_date, monkeypatch):
    """两处实现都必须正确处理 UTC→北京时间的午夜边界——这是 biz_today() 的核心职责。

    Cloud 容器时区是 UTC，直接 date.today() 会让北京时间 00:00–07:59 的"今天"
    错成昨天。两处实现都必须用 UTC+8 而非裸 date.today()。
    """
    fixed_utc = datetime.fromisoformat(utc_iso)

    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(web_dates, "datetime", FakeDateTime)
    monkeypatch.setattr(dca_types, "datetime", FakeDateTime)

    assert web_dates.biz_today().isoformat() == expected_beijing_date
    assert dca_types.biz_today().isoformat() == expected_beijing_date

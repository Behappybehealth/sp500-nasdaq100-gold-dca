# -*- coding: utf-8 -*-
"""业务日期：全链路唯一"今天"（Asia/Shanghai 自然日，固定 UTC+8）。

Cloud 容器时区是 UTC——直接 `date.today()` 会让北京时间 00:00–07:59 的
"今天"错成昨天（记账日期、预算月份、月度已投全部跟着错）。

中国 1991 年后无夏令时，固定 +8 与 zoneinfo 等价且零依赖（Windows 无系统
IANA 库）。引擎（scripts/dca_calculator.py）因子进程隔离另持一份同规则
实现，两处必须同改。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

_BIZ_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai（无夏令时）


def biz_today() -> date:
    """业务"今天"：Asia/Shanghai 自然日。"""
    return datetime.now(_BIZ_TZ).date()

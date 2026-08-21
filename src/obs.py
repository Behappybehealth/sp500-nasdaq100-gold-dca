# -*- coding: utf-8 -*-
"""运行日志配置：结构化单行事件，stderr + logs/dca.log 双落点。

本模块**只负责配置 handler**，不提供 emitter——各模块直接 `logging.getLogger("dca.<频道>")`。
这样 `storage.py`（数据层）不必反向 import `src/`（业务层），层次不倒挂；
频道名写死成 `dca.*` 而不用 `__name__`，是为了让所有日志挂在同一棵子树上，
配一次 handler 就全覆盖，同时不碰 root logger（否则 gspread / urllib3 的噪声全涌进来）。

两个落点各有必要性，缺一不可：
- **stderr**：Streamlit Community Cloud 只把进程 stderr 收进日志面板，这是线上唯一能看到日志的地方
- **logs/dca.log**：本机长期部署要的持久化（`*.log` 不入库）；Cloud 容器重启即失，那边只是顺带

行格式：`2026-08-21T10:30:00+0800 ERROR dca.storage sheet_read_failed table=transactions err=...`
`事件名 key=value` 是**书写约定而非框架**——十几个调用点用约定足够，
包一层 emitter 换来的一致性抵不上多一层耦合。

**绝不记 PIN、PIN 哈希与盐**：认证相关只记账号名与结果码。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT = "dca"  # 日志子树根；各模块用 logging.getLogger("dca.storage") 这类挂上来
_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    """幂等地配置 `dca` 日志子树，由入口（app.py）启动时调一次。

    幂等是硬要求，不是优化：Streamlit 每次交互都把整个脚本从头重跑，
    不做 handler 去重的话，日志行数会随交互次数成倍增长。
    """
    root = logging.getLogger(_ROOT)
    if root.handlers:
        return
    root.setLevel(level)
    root.propagate = False  # 不向 Streamlit 的 root logger 冒泡，避免同一行打两遍
    fmt = logging.Formatter(_FMT, datefmt=_DATEFMT)

    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(fmt)
    root.addHandler(err)

    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # 必须带轮转：BUG-017 原文的另一半正是"日志无上限写满磁盘"，
        # 不轮转等于把刚随 BUG-012 删掉的毛病重新请回来。
        fh = RotatingFileHandler(
            log_dir / "dca.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        # 落盘不可用不能阻断应用：stderr 那路已经生效，日志能力降级但不消失。
        root.warning("log_file_unavailable dir=%s err=%s", log_dir, e)

# -*- coding: utf-8 -*-
"""应用级上下文：启动期路径与配置（Paths）+ 侧栏产出的决策结果（Decision）。

拆分设计（docs/plans/app-split-design.md 决策 1/2）：
- 子模块一律显式收 ctx 参数，禁止 `from app import *`（app.py 是脚本不是模块，import 它会二次执行）
- 本文件**不 import streamlit**，可脱离 UI 单独做单元测试
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    """启动期确定、全程不变的路径与配置。"""

    base: Path  # --base-dir 或代码目录
    code_dir: Path  # 代码目录（scripts/ 在这里）
    data_dir: Path
    tx_csv: Path
    backtest_dir: Path
    config: dict
    assets: dict  # = config["assets"]


@dataclass
class Decision:
    """侧栏跑模型产出的决策结果（由 sidebar.render 返回；手填金额时整体重算）。"""

    result: dict  # run_model 的原始 JSON
    dec: dict  # result["decision"]
    ms: dict  # result["monthly_budget_status"]
    pf: dict  # result["portfolio"]


def build_paths(argv=None) -> Paths:
    """解析 --base-dir → 定位数据目录 → 读 config.json。

    ⚠️ 唯一的真实陷阱：CODE_DIR 必须是**项目根**——本文件在 src/ 下，
    所以要 parent.parent（多一层），不能照抄单层脚本的 parent。
    """
    code_dir = Path(__file__).resolve().parent.parent  # src/ 的上一级 = 项目根

    # 解析 --base-dir（Streamlit 用 -- 分隔自定义参数）
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=None, help="用户独立数据目录（多用户部署时用）")
    args, _ = ap.parse_known_args(argv)
    base = Path(args.base_dir).resolve() if args.base_dir else code_dir
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        config = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise SystemExit(f"缺少或损坏的 data/config.json：{e}") from None

    # 回测结果：优先用户数据目录下的 backtest/（多用户部署可各自覆盖），否则用仓库自带那份。
    # 不要再用相对代码目录上跳的路径 —— 那会跟着代码搬家而失效。
    bt_override = base / "backtest"
    backtest_dir = bt_override if bt_override.exists() else code_dir / "backtest"

    return Paths(
        base=base,
        code_dir=code_dir,
        data_dir=data_dir,
        tx_csv=data_dir / "transactions.csv",
        backtest_dir=backtest_dir,
        config=config,
        assets=config["assets"],
    )

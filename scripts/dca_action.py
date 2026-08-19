# -*- coding: utf-8 -*-
"""业务动作 CLI 薄壳：Claude Skill 入口经它与 Web 共用同一业务层（storage.py）。

子命令：
  record tx    记一笔成交（字段与 Web 记账 Tab 一致）
  record obs   记一条观察/跳过
  override     设置月度预算覆盖（按月起算，长期生效）

约定：
- 输出单行 JSON 到 stdout：{"ok": true, ...} 或 {"ok": false, "error": ...}，退出码 0/1
- 保持进程隔离：Skill 用 subprocess 调本脚本，不跨项目 import
- --user 缺省取环境变量 DCA_USER，再缺省 "local"（单机）
- 云端模式（base-dir 下配了 .streamlit/secrets.toml）写入 Google Sheets 后
  会自动 sync_local 刷新本地落盘缓存，保证下一次引擎复盘读到刚写入的记录；
  无 secrets 时回退本地 CSV（data/transactions.csv 等），与引擎本地读取口径一致
- st.secrets 按当前工作目录解析 secrets.toml，所以 main() 先 chdir(base_dir) 再调 storage
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dca_calculator import biz_today  # 同目录直接运行时 scripts/ 在 sys.path[0]；业务"今天"唯一定义


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dca_action",
        description="定投业务动作 CLI（记账 / 预算覆盖），Web 与 Skill 共用 storage.py 业务层",
    )
    p.add_argument(
        "--base-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="项目根目录（含 data/ 与 .streamlit/）",
    )
    p.add_argument(
        "--user",
        default=os.environ.get("DCA_USER", "local"),
        help="记录归属用户（云端模式必填真实用户名；缺省取 DCA_USER，再缺省 local）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="记账（成交 / 观察）")
    rec_sub = rec.add_subparsers(dest="kind", required=True)

    tx = rec_sub.add_parser("tx", help="记一笔成交（买入/卖出）")
    tx.add_argument("--date", default=str(biz_today()))
    tx.add_argument("--action", choices=["buy", "sell"], required=True)
    tx.add_argument("--asset", required=True, help="资产配置键，如 sp500 / nasdaq100 / gold")
    tx.add_argument("--symbol", default=None, help="交易代码；缺省取 config 里该资产的 symbol")
    tx.add_argument("--amount", type=float, required=True, help="金额 RMB")
    tx.add_argument("--price", type=float, required=True, help="净值价格 U")
    tx.add_argument("--shares", type=float, default=0.0, help="数量；0 = 按 金额÷汇率÷价格 自动算")
    tx.add_argument("--fee", type=float, default=0.0, help="手续费 RMB")
    tx.add_argument("--fx", type=float, default=None, help="汇率（U/CNY 或 USD/CNY）；自动算份额时必填")
    tx.add_argument("--notes", default="")
    tx.add_argument(
        "--force",
        action="store_true",
        help="同日同资产同方向已有记录时仍写入（确认为新一笔）；缺省遇重复直接报错",
    )

    obs = rec_sub.add_parser("obs", help="记一条观察/跳过")
    obs.add_argument("--date", default=str(biz_today()))
    obs.add_argument("--reason", default="", help="跳过原因（通常带当时信号等级）")
    obs.add_argument("--notes", default="")
    obs.add_argument("--total-suggested", type=float, default=0.0, help="当时建议总金额 RMB")
    obs.add_argument("--decision-level", default="", help="当时信号等级标签")
    obs.add_argument("--w-sp500", type=float, default=0.0, help="当时建议权重：标普500")
    obs.add_argument("--w-ndx", type=float, default=0.0, help="当时建议权重：纳指100")
    obs.add_argument("--w-gold", type=float, default=0.0, help="当时建议权重：黄金")

    ov = sub.add_parser("override", help="设置月度预算覆盖")
    ov.add_argument("--amount", type=float, required=True, help="月预算 RMB")
    ov.add_argument("--month", default=biz_today().strftime("%Y-%m"), help="生效月 YYYY-MM（含）起长期生效")

    return p


def _fail(msg: str) -> int:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    return 1


def main() -> int:
    args = _build_parser().parse_args()
    base_dir = args.base_dir.resolve()
    if not (base_dir / "data").is_dir():
        return _fail(f"base-dir 下没有 data/ 目录：{base_dir}")

    # st.secrets 按 CWD 找 .streamlit/secrets.toml——先切到项目根，secrets 解析才与 Web 一致
    os.chdir(base_dir)
    # storage.py 在项目根（脚本所在目录的上一级）；脚本直接运行时 sys.path[0] 是 scripts/，需补项目根
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import storage  # 切目录后再走 storage 调用（import 本身不触 secrets）

    storage.init(base_dir / "data")
    backend = "sheets" if storage.sheets_enabled() else "local"

    try:
        if args.cmd == "record" and args.kind in ("tx", "obs"):
            try:
                date.fromisoformat(args.date)
            except ValueError:
                return _fail(f"--date「{args.date}」不是合法的 YYYY-MM-DD，未写入")

        if args.cmd == "record" and args.kind == "tx":
            if args.amount <= 0 or args.price <= 0:
                return _fail("金额和净值价格必须为正数")
            symbol = args.symbol
            if not symbol:
                config = json.loads(
                    (base_dir / "data" / "config.json").read_text(encoding="utf-8")
                )
                asset_cfg = config.get("assets", {}).get(args.asset)
                if not asset_cfg:
                    return _fail(f"config.json 里没有资产 {args.asset}，且未显式给 --symbol")
                symbol = asset_cfg["symbol"]
            shares = args.shares
            if not shares:
                if not args.fx:
                    return _fail("未给 --shares 时必须给 --fx（按 金额÷汇率÷价格 自动算份额）")
                shares = round(args.amount / args.fx / args.price, 6)
            row = {
                "date": args.date,
                "action": args.action,
                "asset": args.asset,
                "symbol": symbol,
                "currency": "USDT",
                "amount_rmb": args.amount,
                "price": args.price,
                "shares": shares,
                "fee_rmb": args.fee,
                "fx_rate": args.fx,
                "notes": args.notes,
            }
            table = "transactions"
        elif args.cmd == "record" and args.kind == "obs":
            row = {
                "date": args.date,
                "action": "observe",
                "total_suggested_rmb": args.total_suggested,
                "user_amount_rmb": 0,
                "decision_level": args.decision_level,
                "sp500_weight": args.w_sp500,
                "ndx100_weight": args.w_ndx,
                "gold_weight": args.w_gold,
                "reason": args.reason,
                "notes": args.notes,
            }
            table = "observations"
        elif args.cmd == "override":
            if args.amount <= 0:
                return _fail("预算金额必须为正数")
            row = None
            table = "budget_overrides"
        else:  # pragma: no cover - argparse 已挡住
            return _fail(f"未知子命令：{args.cmd}")

        if table == "budget_overrides":
            storage.set_override(args.user, args.month, args.amount)
        else:
            storage.append_row(table, args.user, row, force=getattr(args, "force", False))

        # 云端写入后刷新本地落盘缓存，下一次引擎复盘才能读到刚写入的记录
        synced = None
        if backend == "sheets":
            try:
                storage.sync_local(args.user)
                synced = True
            except Exception as e:  # 同步失败不回滚已写入的数据，但必须可见
                synced = False
                print(
                    json.dumps(
                        {"ok": True, "table": table, "row": row, "backend": backend,
                         "synced": False, "sync_error": str(e)},
                        ensure_ascii=False,
                    )
                )
                return 0

        out = {"ok": True, "table": table, "backend": backend, "synced": synced}
        if table == "budget_overrides":
            out["month"] = args.month
            out["amount"] = args.amount
        else:
            out["row"] = row
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except ValueError as e:
        # 去重拦截等业务校验错误：原样透出（Skill 侧据文案决定是否加 --force 重试）
        return _fail(str(e))
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())

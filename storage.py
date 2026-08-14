# -*- coding: utf-8 -*-
"""存储层：Google Sheets（云端多用户）优先，未配置 secrets 时回退本地 CSV（单机开发）。

工作表结构（同一个 Google 表格，4 个 worksheet，所有数据表带 user 列做隔离）：
- users:            name, pin_hash, created_at
- transactions:     user, date, action, asset, symbol, currency, amount_rmb, price, shares, fee_rmb, fx_rate, notes
- observations:     user, date, action, total_suggested_rmb, user_amount_rmb, decision_level, sp500_weight, ndx100_weight, gold_weight, reason, notes
- budget_overrides: user, month, budget_rmb

设计要点：
- Sheets 是唯一事实源；本地 CSV/JSON 只是给 dca_calculator 子进程用的同步缓存。
- 写操作 = 先写 Sheets 再同步到本地缓存；sync_local() 会把云端该用户的数据落盘。
- 回退模式（无 secrets）行为与旧版完全一致：纯本地 CSV，无用户概念。
- 注意：Sheets 连接基于整表 read/update，并发写存在理论上的竞态；本工具面向极小团队，可接受。
"""
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

TX_FIELDS = ["user", "date", "action", "asset", "symbol", "currency",
             "amount_rmb", "price", "shares", "fee_rmb", "fx_rate", "notes"]
OBS_FIELDS = ["user", "date", "action", "total_suggested_rmb", "user_amount_rmb",
              "decision_level", "sp500_weight", "ndx100_weight", "gold_weight",
              "reason", "notes"]
OVR_FIELDS = ["user", "month", "budget_rmb"]
USER_FIELDS = ["name", "pin_hash", "role", "created_at"]

TABLES = {"transactions": TX_FIELDS, "observations": OBS_FIELDS}


# ---------- 后端探测 ----------

def sheets_enabled() -> bool:
    """secrets 里配置了 [connections.gsheets] 才启用云端模式。"""
    try:
        return "gsheets" in st.secrets.get("connections", {})
    except Exception:
        return False


@st.cache_resource
def _conn():
    # 延迟导入：未安装 st-gsheets-connection 时本地 CSV 模式仍可用
    from streamlit_gsheets import GSheetsConnection
    import streamlit_gsheets.gsheets_connection as _gc
    # 该库内部 @cache_data 没关 show_spinner，会把 Running GSheetsServiceAccountClient...
    # 这类内部函数名闪到前端；给它打个静默补丁（_get_as_dataframe 在调用时才装饰，补丁有效）
    if not getattr(_gc, "_quiet_patched", False):
        _orig_cache_data = _gc.cache_data
        def _quiet_cache_data(*args, **kwargs):
            kwargs["show_spinner"] = False
            return _orig_cache_data(*args, **kwargs)
        _gc.cache_data = _quiet_cache_data
        _gc._quiet_patched = True
    return st.connection("gsheets", type=GSheetsConnection)


# ---------- Sheets 底层读写 ----------
#
# 进程内短缓存（8 秒）：一次页面渲染里 list_users/is_admin/is_activated 等会多次读同一张
# 表，不缓存会产生 N 次 Google 往返、页面明显卡。写操作后即时失效，多用户场景 8 秒
# 内的一致性窗口对本应用（单管理员、极小团队）可接受。
_SHEET_CACHE: dict = {}
_SHEET_CACHE_TTL = 8.0


def _read_ws(name: str, fields: list) -> pd.DataFrame:
    """读整个 worksheet；不存在或为空时返回带表头的空表。"""
    _hit = _SHEET_CACHE.get(name)
    if _hit is not None and (time.time() - _hit[0]) < _SHEET_CACHE_TTL:
        return _hit[1].copy()
    try:
        df = _conn().read(worksheet=name, ttl=0)
    except Exception:
        return pd.DataFrame(columns=fields)
    if df is None or df.empty:
        return pd.DataFrame(columns=fields)
    df = df.dropna(how="all")
    for f in fields:
        if f not in df.columns:
            df[f] = ""
    df = df[fields].fillna("").astype(str)
    _SHEET_CACHE[name] = (time.time(), df)
    return df.copy()


def _write_ws(name: str, df: pd.DataFrame) -> None:
    _SHEET_CACHE.pop(name, None)  # 写后即时失效，下一次读拿最新
    try:
        _conn().update(worksheet=name, data=df)
    except Exception:
        # worksheet 不存在 → 创建
        _conn().create(worksheet=name, data=df)


# ---------- 用户（名字 + PIN）----------

def _pin_hash(name: str, pin: str) -> str:
    return hashlib.sha256(f"dca::{name.strip()}::{pin}".encode("utf-8")).hexdigest()


def _match_user(df: pd.DataFrame, name: str):
    """大小写不敏感 + 去首尾空格的名字匹配，返回布尔掩码。"""
    key = (name or "").strip().casefold()
    return df["name"].astype(str).str.strip().str.casefold() == key


def list_users() -> list:
    if not sheets_enabled():
        return []
    return [n for n in _read_ws("users", USER_FIELDS)["name"].tolist() if n]


def verify_user(name: str, pin: str) -> bool:
    df = _read_ws("users", USER_FIELDS)
    hit = df[_match_user(df, name)]
    if hit.empty:
        return False
    stored = hit.iloc[0]["name"]
    return hit.iloc[0]["pin_hash"] == _pin_hash(stored, pin)


def create_user(name: str, pin: str, pin_confirm: str, role: str = "user"):
    """返回 (ok, msg)。名字唯一（不区分大小写）；PIN 要求 4-8 位。role: user|admin"""
    name = (name or "").strip()
    if not name:
        return False, "名字不能为空"
    if pin != pin_confirm:
        return False, "两次输入的 PIN 不一致"
    if not (4 <= len(pin or "") <= 8):
        return False, "PIN 需要 4-8 位"
    df = _read_ws("users", USER_FIELDS)
    if _match_user(df, name).any():
        return False, "这个名字已存在"
    row = pd.DataFrame([{"name": name, "pin_hash": _pin_hash(name, pin), "role": role,
                         "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])
    _write_ws("users", pd.concat([df, row], ignore_index=True))
    return True, "ok"


def is_admin(name: str) -> bool:
    df = _read_ws("users", USER_FIELDS)
    hit = df[_match_user(df, name)]
    return not hit.empty and hit.iloc[0].get("role", "") == "admin"


def admin_add_user(name: str):
    """管理员只加名字；PIN 由用户首次登录时自己设置（pin_hash 空 = 待激活）。"""
    name = (name or "").strip()
    if not name:
        return False, "名字不能为空"
    df = _read_ws("users", USER_FIELDS)
    if _match_user(df, name).any():
        return False, "这个名字已存在"
    row = pd.DataFrame([{"name": name, "pin_hash": "", "role": "user",
                         "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])
    _write_ws("users", pd.concat([df, row], ignore_index=True))
    return True, "ok"


def is_activated(name: str) -> bool:
    """已设置过 PIN 才算已激活。"""
    df = _read_ws("users", USER_FIELDS)
    hit = df[_match_user(df, name)]
    return not hit.empty and bool(hit.iloc[0].get("pin_hash", ""))


def set_pin(name: str, pin: str, pin_confirm: str):
    """用户首次激活时自己设置 PIN；仅当账号处于待激活状态才允许。"""
    if pin != pin_confirm:
        return False, "两次输入的 PIN 不一致"
    if not (4 <= len(pin or "") <= 8):
        return False, "PIN 需要 4-8 位"
    df = _read_ws("users", USER_FIELDS)
    hit = _match_user(df, name)
    if not hit.any():
        return False, "用户不存在"
    if df.loc[hit, "pin_hash"].iloc[0]:
        return False, "该账号已激活，请联系管理员重置"
    stored = df.loc[hit, "name"].iloc[0]
    df.loc[hit, "pin_hash"] = _pin_hash(stored, pin)
    _write_ws("users", df)
    return True, "ok"


def delete_user(name: str):
    df = _read_ws("users", USER_FIELDS)
    hit = _match_user(df, name)
    if not hit.any():
        return False, "用户不存在"
    _write_ws("users", df[~hit].reset_index(drop=True))
    return True, "ok"


def reset_pin(name: str):
    """管理员重置：清空 PIN 回到待激活，用户下次登录重新自己设置。"""
    df = _read_ws("users", USER_FIELDS)
    hit = _match_user(df, name)
    if not hit.any():
        return False, "用户不存在"
    df.loc[hit, "pin_hash"] = ""
    _write_ws("users", df)
    return True, "ok"


# ---------- 数据读写（统一入口）----------

def read_rows(table: str, user: str) -> list:
    """返回该用户的全部行（list[dict]，云端数据已按 user 过滤）。"""
    fields = TABLES[table]
    if sheets_enabled():
        df = _read_ws(table, fields)
        df = df[df["user"] == user]
        return df.to_dict("records")
    path = _local_csv(table)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def append_row(table: str, user: str, row: dict) -> None:
    fields = TABLES[table]
    full = {f: row.get(f, "") for f in fields}
    full["user"] = user
    if sheets_enabled():
        df = _read_ws(table, fields)
        df = pd.concat([df, pd.DataFrame([full])], ignore_index=True)
        _write_ws(table, df)
        sync_local(user)  # 让本地缓存与云端一致
    else:
        # 本地模式保持旧 schema（不带 user 列）
        path = _local_csv(table)
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields[1:])
            if not exists:
                w.writeheader()
            w.writerow({k: v for k, v in full.items() if k != "user"})


def get_overrides(user: str) -> dict:
    """预算覆盖 {month: budget_rmb}。"""
    if sheets_enabled():
        df = _read_ws("budget_overrides", OVR_FIELDS)
        df = df[df["user"] == user]
        return {r["month"]: float(r["budget_rmb"]) for _, r in df.iterrows() if r["month"]}
    path = _local_json("budget_overrides.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_override(user: str, month: str, budget_rmb: float) -> None:
    if sheets_enabled():
        df = _read_ws("budget_overrides", OVR_FIELDS)
        keep = df[~((df["user"] == user) & (df["month"] == month))]
        row = pd.DataFrame([{"user": user, "month": month, "budget_rmb": str(budget_rmb)}])
        _write_ws("budget_overrides", pd.concat([keep, row], ignore_index=True))
        sync_local(user)
    else:
        path = _local_json("budget_overrides.json")
        try:
            overrides = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            overrides = {}
        overrides[month] = float(budget_rmb)
        path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 云端 → 本地缓存同步（供 dca_calculator 子进程读）----------

def sync_local(user: str) -> None:
    """把云端该用户的 transactions/observations/budget_overrides 落盘成本地缓存。

    覆盖前先备份一次（.localbak），防止误操作丢本地历史。
    """
    if not sheets_enabled():
        return
    for table in TABLES:
        rows = read_rows(table, user)
        path = _local_csv(table)
        if path.exists() and not path.with_suffix(path.suffix + ".localbak").exists():
            path.replace(path.with_suffix(path.suffix + ".localbak"))
            # 备份后原路径会被重新写出
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TABLES[table])
            w.writeheader()
            w.writerows(rows)
    ov = get_overrides(user)
    _local_json("budget_overrides.json").write_text(
        json.dumps(ov, ensure_ascii=False, indent=2), encoding="utf-8")


def import_local_to_sheets(user: str) -> dict:
    """一次性迁移：把本地 CSV/JSON 的历史数据上传到云端该用户名下。返回各表行数。"""
    counts = {}
    for table in TABLES:
        path = _local_csv(table)
        rows = []
        if path.exists() and path.stat().st_size > 0:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        for r in rows:
            r["user"] = user
        if rows:
            df = _read_ws(table, TABLES[table])
            df = pd.concat([df, pd.DataFrame(rows)[TABLES[table]].fillna("").astype(str)],
                           ignore_index=True)
            _write_ws(table, df)
        counts[table] = len(rows)
    ov_path = _local_json("budget_overrides.json")
    try:
        ov = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    except Exception:
        ov = {}
    if ov:
        df = _read_ws("budget_overrides", OVR_FIELDS)
        rows = [{"user": user, "month": m, "budget_rmb": str(v)} for m, v in ov.items()]
        _write_ws("budget_overrides", pd.concat([df, pd.DataFrame(rows)], ignore_index=True))
    counts["budget_overrides"] = len(ov)
    sync_local(user)
    return counts


# ---------- 本地路径 ----------

_LOCAL_BASE = Path(__file__).resolve().parent / "data"


def init(data_dir) -> None:
    """由 app.py 启动时调用，指定本地缓存目录（跟随 --base-dir）。"""
    global _LOCAL_BASE
    _LOCAL_BASE = Path(data_dir)


def _local_csv(table: str) -> Path:
    return _LOCAL_BASE / f"{table}.csv"


def _local_json(name: str) -> Path:
    return _LOCAL_BASE / name

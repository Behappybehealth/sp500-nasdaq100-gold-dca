# -*- coding: utf-8 -*-
"""存储层：Google Sheets（云端多用户）优先，未配置 secrets 时回退本地 CSV（单机开发）。

工作表结构（同一个 Google 表格，所有数据表带 user 列做隔离）：
- users:            name, pin_hash, salt, hash_algo, role, fail_count, locked_until, created_at
- transactions:     user, date, action, asset, symbol, currency, amount_rmb, price, shares, fee_rmb, fx_rate, notes
- observations:     user, date, action, total_suggested_rmb, user_amount_rmb, decision_level, sp500_weight, ndx100_weight, gold_weight, reason, notes
- budget_overrides: user, month, budget_rmb
- <任意表>_bak:      写前快照（滚动单份，覆写前自动留底，见 _write_ws）

设计要点：
- Sheets 是唯一事实源；本地 CSV/JSON 只是给 dca_calculator 子进程用的同步缓存。
- 写操作 = 先写 Sheets 再同步到本地缓存；sync_local() 会把云端该用户的数据落盘到 data/users/<user>/。
- 回退模式（无 secrets）行为与旧版完全一致：纯本地 CSV，无用户概念。
- 读失败与空表严格区分：读失败抛 SheetReadError，绝不伪装成空表（否则一次网络抖动 = 全表被覆写）。
- 认证 fail-closed：PIN 用 PBKDF2+随机盐（旧 sha256 账号登录时自动迁移），连续失败 5 次锁 15 分钟。
- 注意：Sheets 连接基于整表 read/update，并发写存在理论上的竞态；本工具面向极小团队，可接受。
"""

import csv
import hashlib
import json
import logging
import secrets
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from gspread.exceptions import WorksheetNotFound as _WsNotFound
except Exception:  # pragma: no cover - gspread 是 st-gsheets-connection 的硬依赖，本地纯 CSV 模式可能没装
    _WsNotFound = None  # type: ignore[assignment]


# 日志频道（配置在 src/obs.py，由 app.py 启动时调一次）。
# 这里只 getLogger、不 import src/——数据层不反向依赖业务层。未配置 handler 时静默无输出，不报错。
_log = logging.getLogger("dca.storage")


class SheetReadError(RuntimeError):
    """Sheets 读取失败（网络/配额/凭据）。与「表真的是空的」严格区分——上层绝不可把它当空表继续写。"""


class SheetWriteError(RuntimeError):
    """Sheets 写入或写前快照失败（含快照失败触发的放弃写入）。"""

TX_FIELDS = [
    "user",
    "date",
    "action",
    "asset",
    "symbol",
    "currency",
    "amount_rmb",
    "price",
    "shares",
    "fee_rmb",
    "fx_rate",
    "notes",
]
OBS_FIELDS = [
    "user",
    "date",
    "action",
    "total_suggested_rmb",
    "user_amount_rmb",
    "decision_level",
    "sp500_weight",
    "ndx100_weight",
    "gold_weight",
    "reason",
    "notes",
]
OVR_FIELDS = ["user", "month", "budget_rmb"]
USER_FIELDS = [
    "name",
    "pin_hash",
    "role",
    "created_at",
    "salt",
    "hash_algo",
    "fail_count",
    "locked_until",
]

TABLES = {"transactions": TX_FIELDS, "observations": OBS_FIELDS}


def build_tx_row(*, date, action, asset, symbol, amount_rmb, price,
             shares, fee_rmb, fx_rate, notes="", currency="USDT") -> dict:
    """构造 transactions 行（字段与 TX_FIELDS 对齐，单一事实源）。

    Web 记账 Tab 与 CLI（dca_action.py）共用此函数，避免三处各写一遍字段字典。
    """
    return {
        "date": date, "action": action, "asset": asset, "symbol": symbol,
        "currency": currency, "amount_rmb": amount_rmb, "price": price,
        "shares": shares, "fee_rmb": fee_rmb, "fx_rate": fx_rate, "notes": notes,
    }


def build_obs_row(*, date, decision_level, total_suggested_rmb,
                  weights: dict, reason, notes="", user_amount_rmb=0) -> dict:
    """构造 observations 行（字段与 OBS_FIELDS 对齐，单一事实源）。

    weights 接收 {sp500/nasdaq100/gold: w} 形式，内部按 OBS_FIELDS 的键名
    （ndx100_weight）对齐并四舍五入到 4 位。
    """
    return {
        "date": date, "action": "observe",
        "total_suggested_rmb": total_suggested_rmb,
        "user_amount_rmb": user_amount_rmb,
        "decision_level": decision_level,
        "sp500_weight": round(weights.get("sp500", 0), 4),
        "ndx100_weight": round(weights.get("nasdaq100", 0), 4),
        "gold_weight": round(weights.get("gold", 0), 4),
        "reason": reason, "notes": notes,
    }

# PIN 策略：新 PIN 强制 6-8 位；存量 4-5 位旧账号不受影响，登录成功时自动迁移哈希。
_PBKDF2_ITER = 200_000  # 2026-08-17 本机实测约 0.073s/次；换部署机应按 0.05-0.3s 目标重新标定
_PIN_MIN, _PIN_MAX = 6, 8
_LOCK_AFTER = 5  # 连续失败 5 次锁定
_LOCK_MINUTES = 15


# ---------- 后端探测 ----------


def sheets_status() -> str:
    """后端三态：'ok' 已配置 / 'off' 未配置（合法单机）/ 'error' secrets 读取异常（配置损坏）。

    「没配」和「读不出来」必须分开：前者是用户选的本地模式，后者是故障——
    安全相关的判断只许信 'ok'（fail-closed）。"""
    try:
        conns = st.secrets.get("connections", {})
    except Exception:
        return "error"
    return "ok" if "gsheets" in conns else "off"


def sheets_enabled() -> bool:
    """secrets 里配置了 [connections.gsheets] 才启用云端模式；读取异常一律按未启用（不再静默吞错，状态可查 sheets_status）。"""
    return sheets_status() == "ok"


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
        _gc._quiet_patched = True  # type: ignore[attr-defined]  # 猴子补丁标记，pyright 不认模块动态属性
    return st.connection("gsheets", type=GSheetsConnection)


# ---------- Sheets 底层读写 ----------
#
# 进程内短缓存（8 秒）：一次页面渲染里 list_users/is_admin/is_activated 等会多次读同一张
# 表，不缓存会产生 N 次 Google 往返、页面明显卡。写操作后即时失效，多用户场景 8 秒
# 内的一致性窗口对本应用（单管理员、极小团队）可接受。
_SHEET_CACHE: dict = {}
_SHEET_CACHE_TTL = 8.0


def _is_missing_ws(exc: Exception) -> bool:
    """worksheet 不存在（首次使用还没建表）= 合法的空表，区别于读取故障。"""
    return (_WsNotFound is not None and isinstance(exc, _WsNotFound)) or (
        "WorksheetNotFound" in type(exc).__name__
    )


def _read_ws(name: str, fields: list, fresh: bool = False) -> pd.DataFrame:
    """读整个 worksheet；表不存在或为空时返回带表头的空表；读取故障抛 SheetReadError（绝不让"我不知道"伪装成"没有"）。
    fresh=True 绕过 8 秒短缓存强制新鲜读。"""
    if fresh:
        _SHEET_CACHE.pop(name, None)
    _hit = _SHEET_CACHE.get(name)
    if _hit is not None and (time.time() - _hit[0]) < _SHEET_CACHE_TTL:
        return _hit[1].copy()
    try:
        df = _conn().read(worksheet=name, ttl=0)
    except Exception as e:
        if _is_missing_ws(e):
            return pd.DataFrame(columns=fields)
        _log.error("sheet_read_failed table=%s err=%s", name, e)
        raise SheetReadError(f"读取工作表 {name} 失败：{e}") from e
    if df is None or df.empty:
        return pd.DataFrame(columns=fields)
    df = df.dropna(how="all")
    for f in fields:
        if f not in df.columns:
            df[f] = ""
    df = df[fields].fillna("").astype(str)
    _SHEET_CACHE[name] = (time.time(), df)
    return df.copy()  # type: ignore[return-value]  # pandas 切片在 pyright 下是 DataFrame|Series 联合类型


def _write_ws(name: str, df: pd.DataFrame) -> None:
    """整表覆写。写前先把当前内容快照到 <name>_bak（滚动单份）；
    快照失败则放弃写入（fail-closed：宁可不写，不可无备份覆写）。"""
    _SHEET_CACHE.pop(name, None)  # 写后即时失效，下一次读拿最新
    conn = _conn()
    try:
        cur = conn.read(worksheet=name, ttl=0)
    except Exception as e:
        if _is_missing_ws(e):
            cur = None  # 首次写这张表，没有可快照的内容
        else:
            _log.error("write_precheck_read_failed table=%s err=%s", name, e)
            raise SheetWriteError(f"写前读取 {name} 失败，已放弃写入：{e}") from e
    if cur is not None and not cur.dropna(how="all").empty:
        bak = f"{name}_bak"
        try:
            try:
                conn.update(worksheet=bak, data=cur)
            except Exception:
                conn.create(worksheet=bak, data=cur)
        except Exception as e:
            _log.error("write_snapshot_failed table=%s bak=%s err=%s", name, bak, e)
            raise SheetWriteError(f"写前快照 {bak} 失败，已放弃写入 {name}：{e}") from e
        _SHEET_CACHE.pop(bak, None)
    try:
        conn.update(worksheet=name, data=df.fillna(""))
    except Exception:
        try:
            conn.create(worksheet=name, data=df.fillna(""))
        except Exception as e:
            _log.error("sheet_write_failed table=%s err=%s", name, e)
            raise SheetWriteError(f"写入工作表 {name} 失败：{e}") from e


# ---------- 用户（名字 + PIN）----------


def _pin_hash(name: str, pin: str) -> str:
    """旧格式（sha256_v1）：单轮 SHA256 无随机盐，仅为校验存量账号保留；新账号一律走 _new_pin_record。"""
    return hashlib.sha256(f"dca::{name.strip()}::{pin}".encode()).hexdigest()


def _pin_hash_v2(pin: str, salt_hex: str) -> str:
    """PBKDF2-HMAC-SHA256 + 每账号随机盐。慢是刻意的：把离线穷举成本抬高 20 万倍。"""
    return hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITER
    ).hex()


def _new_pin_record(pin: str) -> dict:
    """新 PIN 的三字段（每账号独立随机盐；盐不需要保密，只需不可预测）。"""
    salt = secrets.token_hex(16)
    return {
        "pin_hash": _pin_hash_v2(pin, salt),
        "salt": salt,
        "hash_algo": "pbkdf2_v1",
    }


def _parse_ts(s) -> "datetime | None":
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def _fail_count_of(row) -> int:
    try:
        return int(float(row.get("fail_count") or 0))
    except (TypeError, ValueError):
        return 0


def _match_user(df: pd.DataFrame, name: str):
    """大小写不敏感 + 去首尾空格的名字匹配，返回布尔掩码。"""
    key = (name or "").strip().casefold()
    return df["name"].astype(str).str.strip().str.casefold() == key


def list_users() -> list:
    if not sheets_enabled():
        return []
    return [n for n in _read_ws("users", USER_FIELDS)["name"].tolist() if n]


def list_users_fresh() -> list:
    """绕过 8 秒短缓存的新鲜用户名单（登录第二阶段、自举防呆用）。"""
    if not sheets_enabled():
        return []
    return [n for n in _read_ws("users", USER_FIELDS, fresh=True)["name"].tolist() if n]


def authenticate(name: str, pin: str):
    """登录校验：一次新鲜读取 users 表完成 锁定/存在性/激活态/PIN 四重判断。
    返回 (status, canon_name, names)；status ∈ ok|no_user|pending|bad_pin|locked，
    names 为这次新鲜读到的用户名列表（调用方拿来刷新会话缓存）。
    旧格式（sha256_v1）账号验证通过即自动迁移为 pbkdf2_v1（用户无感知）。"""
    df = _read_ws("users", USER_FIELDS, fresh=True)
    names = [n for n in df["name"].tolist() if n]
    hit = _match_user(df, name)
    if not hit.any():
        return "no_user", None, names
    idx = df.index[hit][0]
    row = df.loc[idx]
    stored = row["name"]
    # 锁定期内的账号无论 PIN 对错一律拒绝（限速是本层的第二道闸）
    locked_until = _parse_ts(row.get("locked_until", ""))
    if locked_until and datetime.now(timezone.utc) < locked_until:
        return "locked", stored, names
    if not row["pin_hash"]:
        return "pending", stored, names
    algo = row.get("hash_algo") or "sha256_v1"  # 空 = 旧格式账号
    if algo == "sha256_v1":
        ok = row["pin_hash"] == _pin_hash(stored, pin)
    else:
        ok = bool(row.get("salt")) and row["pin_hash"] == _pin_hash_v2(
            pin, row["salt"]
        )
    if not ok:
        fails = _fail_count_of(row) + 1
        df.loc[idx, "fail_count"] = str(fails)
        if fails >= _LOCK_AFTER:
            df.loc[idx, "locked_until"] = (
                datetime.now(timezone.utc) + timedelta(minutes=_LOCK_MINUTES)
            ).isoformat(timespec="seconds")
            df.loc[idx, "fail_count"] = "0"
        _write_ws("users", df)
        return "bad_pin", stored, names
    # 成功：清失败状态；旧格式顺手迁移成 PBKDF2（rehash on login）
    dirty = False
    if row.get("fail_count") or row.get("locked_until"):
        df.loc[idx, "fail_count"] = ""
        df.loc[idx, "locked_until"] = ""
        dirty = True
    if algo == "sha256_v1":
        for k, v in _new_pin_record(pin).items():
            df.loc[idx, k] = v
        dirty = True
    if dirty:
        _write_ws("users", df)
    return "ok", stored, names


def create_user(name: str, pin: str, pin_confirm: str, role: str = "user"):
    """返回 (ok, msg)。名字唯一（不区分大小写）；新 PIN 强制 6-8 位。role: user|admin"""
    name = (name or "").strip()
    if not name:
        return False, "名字不能为空"
    if pin != pin_confirm:
        return False, "两次输入的 PIN 不一致"
    if not (_PIN_MIN <= len(pin or "") <= _PIN_MAX):
        return False, f"PIN 需要 {_PIN_MIN}-{_PIN_MAX} 位"
    df = _read_ws("users", USER_FIELDS)
    if _match_user(df, name).any():
        return False, "这个名字已存在"
    row = pd.DataFrame(
        [
            {
                "name": name,
                **_new_pin_record(pin),
                "role": role,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "fail_count": "",
                "locked_until": "",
            }
        ]
    )
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
    row = pd.DataFrame(
        [
            {
                "name": name,
                "pin_hash": "",
                "role": "user",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        ]
    )
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
    if not (_PIN_MIN <= len(pin or "") <= _PIN_MAX):
        return False, f"PIN 需要 {_PIN_MIN}-{_PIN_MAX} 位"
    df = _read_ws("users", USER_FIELDS)
    hit = _match_user(df, name)
    if not hit.any():
        return False, "用户不存在"
    if df.loc[hit, "pin_hash"].iloc[0]:
        return False, "该账号已激活，请联系管理员重置"
    rec = _new_pin_record(pin)
    for k, v in rec.items():
        df.loc[hit, k] = v
    df.loc[hit, "fail_count"] = ""
    df.loc[hit, "locked_until"] = ""
    _write_ws("users", df)
    return True, "ok"


def delete_user(name: str):
    df = _read_ws("users", USER_FIELDS)
    hit = _match_user(df, name)
    if not hit.any():
        return False, "用户不存在"
    _write_ws("users", df[~hit].reset_index(drop=True))  # type: ignore[arg-type]
    return True, "ok"


def reset_pin(name: str):
    """管理员重置：清空 PIN 回到待激活，用户下次登录重新自己设置；同时清掉锁定与旧算法标记。"""
    df = _read_ws("users", USER_FIELDS)
    hit = _match_user(df, name)
    if not hit.any():
        return False, "用户不存在"
    df.loc[hit, "pin_hash"] = ""
    df.loc[hit, "salt"] = ""
    df.loc[hit, "hash_algo"] = ""
    df.loc[hit, "fail_count"] = ""
    df.loc[hit, "locked_until"] = ""
    _write_ws("users", df)
    return True, "ok"


# ---------- 数据读写（统一入口）----------


def read_rows(table: str, user: str) -> list:
    """返回该用户的全部行（list[dict]，云端数据已按 user 过滤）。"""
    fields = TABLES[table]
    if sheets_enabled():
        df = _read_ws(table, fields)
        df = df[df["user"] == user]
        return df.to_dict("records")  # type: ignore[call-overload]
    path = _local_csv(table)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def append_row(table: str, user: str, row: dict, force: bool = False) -> None:
    fields = TABLES[table]
    full = {f: row.get(f, "") for f in fields}
    full["user"] = user
    if table == "transactions" and not force:
        # 同日同资产同方向复重检测：重复买入是重复投钱洞的入口；
        # 同日先买后卖（action 不同）属正常序列，不拦。确认为新一笔时传 force=True。
        dup = next(
            (
                r
                for r in read_rows("transactions", user)
                if r.get("date") == full["date"]
                and r.get("asset") == full["asset"]
                and r.get("action") == full["action"]
            ),
            None,
        )
        if dup is not None:
            raise ValueError(
                f"重复记录：{full['date']} 已有 {full['asset']} {full['action']}"
                f"（金额 ¥{dup.get('amount_rmb')}）；确认为新一笔请显式覆盖写入"
            )
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
        try:
            return {
                r["month"]: float(r["budget_rmb"])
                for _, r in df.iterrows()
                if r["month"]
            }  # type: ignore
        except (TypeError, ValueError):
            return {}  # 个别行金额异常不拖垮整个预算读取
    path = _local_json("budget_overrides.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_override(user: str, month: str, budget_rmb: float) -> None:
    if sheets_enabled():
        df = _read_ws("budget_overrides", OVR_FIELDS)
        keep = df[~((df["user"] == user) & (df["month"] == month))]
        row = pd.DataFrame(
            [{"user": user, "month": month, "budget_rmb": str(budget_rmb)}]
        )
        _write_ws("budget_overrides", pd.concat([keep, row], ignore_index=True))  # type: ignore[arg-type]
        sync_local(user)
    else:
        path = _local_json("budget_overrides.json")
        try:
            overrides = (
                json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            )
        except Exception:
            overrides = {}
        try:
            overrides[month] = float(budget_rmb)
        except (TypeError, ValueError):
            return  # 非法金额直接忽略，不写坏本地文件
        path.write_text(
            json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ---------- 云端 → 本地缓存同步（供 dca_calculator 子进程读）----------


def _rotate_backup(path: Path, keep: int = 10) -> None:
    """覆盖前带时间戳**复制**留底（不是移动），滚动保留最近 keep 份。"""
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.stem}.{ts}.localbak"))
    olds = sorted(path.parent.glob(f"{path.stem}.*.localbak"))
    for old in olds[:-keep]:
        old.unlink()


def sync_local(user: str) -> None:
    """把云端该用户的 transactions/observations/budget_overrides 落盘成本地缓存。

    sheets 模式落在 data/users/<user>/ —— 每用户独立目录，两个用户同时在线也不会互相覆写；
    覆盖前先 _rotate_backup 留底。本地模式（无 secrets）不调用本函数。
    """
    if not sheets_enabled():
        return
    base = _LOCAL_BASE / "users" / user
    base.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        rows = read_rows(table, user)
        path = base / f"{table}.csv"
        _rotate_backup(path)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TABLES[table])
            w.writeheader()
            w.writerows(rows)
    ov = get_overrides(user)
    (base / "budget_overrides.json").write_text(
        json.dumps(ov, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
            df = pd.concat(
                [df, pd.DataFrame(rows)[TABLES[table]].fillna("").astype(str)],
                ignore_index=True,
            )
            _write_ws(table, df)  # type: ignore[arg-type]
        counts[table] = len(rows)
    ov_path = _local_json("budget_overrides.json")
    try:
        ov = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    except Exception:
        ov = {}
    if ov:
        df = _read_ws("budget_overrides", OVR_FIELDS)
        rows = [{"user": user, "month": m, "budget_rmb": str(v)} for m, v in ov.items()]
        _write_ws(
            "budget_overrides", pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
        )
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

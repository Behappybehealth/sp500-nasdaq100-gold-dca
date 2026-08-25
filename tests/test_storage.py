"""storage 关键路径回归（离线，绝不碰真实 Google Sheets 与真实 data/）。

三条被覆盖的路径，都是 P0 批修过的地方：
1. **写入去重**（BUG-008）——同日同资产同方向重复记账必须被拦
2. **PIN 哈希**（BUG-002/004）——PBKDF2 + 每账号随机盐
3. **读失败拒写**（BUG-002）——整表覆写模型下，"读不出来"绝不可当"表是空的"

⚠️ **安全前提**：仓库根有真实 `.streamlit/secrets.toml`，pytest 下 `st.secrets` 读得到它，
`sheets_enabled()` 会返回 True。所以每个测试都**显式 monkeypatch `sheets_enabled`**，
从不依赖环境判定——否则测试会写进真实云端表格。
"""

from __future__ import annotations

import csv

import pandas as pd
import pytest

import storage

# ============================================================
# 隔离夹具
# ============================================================

@pytest.fixture
def local_mode(monkeypatch, tmp_path):
    """本地 CSV 模式 + 数据目录指向 tmp：不碰云端、不碰真实 data/。"""
    monkeypatch.setattr(storage, "sheets_enabled", lambda: False)
    monkeypatch.setattr(storage, "_LOCAL_BASE", tmp_path)
    storage._SHEET_CACHE.clear()
    yield tmp_path
    storage._SHEET_CACHE.clear()


class FakeConn:
    """假的 gsheets 连接：可编排读异常、记录所有写动作。"""

    def __init__(self, read_result=None, read_exc: Exception | None = None,
                 fail_update_on: set | None = None):
        self.read_result = read_result
        self.read_exc = read_exc
        self.fail_update_on = fail_update_on or set()
        self.reads: list = []
        self.updates: list = []
        self.creates: list = []

    def read(self, worksheet: str, ttl: int = 0):
        self.reads.append(worksheet)
        if self.read_exc is not None:
            raise self.read_exc
        return self.read_result

    def update(self, worksheet: str, data):
        if worksheet in self.fail_update_on:
            raise RuntimeError(f"update {worksheet} boom")
        self.updates.append(worksheet)

    def create(self, worksheet: str, data):
        if worksheet in self.fail_update_on:
            raise RuntimeError(f"create {worksheet} boom")
        self.creates.append(worksheet)


class WorksheetNotFound(Exception):
    """按类型名模拟 gspread 的"表还没建"——storage._is_missing_ws 认的就是这个名字。"""


@pytest.fixture
def sheets_mode(monkeypatch):
    """云端模式 + 假连接。返回一个装 FakeConn 的工厂。"""
    monkeypatch.setattr(storage, "sheets_enabled", lambda: True)
    storage._SHEET_CACHE.clear()

    def install(**kw) -> FakeConn:
        conn = FakeConn(**kw)
        monkeypatch.setattr(storage, "_conn", lambda: conn)
        return conn

    yield install
    storage._SHEET_CACHE.clear()


TX = {"date": "2026-08-20", "action": "buy", "asset": "sp500", "symbol": "SPY",
      "currency": "U", "amount_rmb": "1234.5", "price": "6500", "shares": "0.19",
      "fee_rmb": "0", "fx_rate": "7.1", "notes": "虚构测试数据"}


def _rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ============================================================
# 1. 写入去重（本地模式）
# ============================================================

def test_append_writes_local_csv_without_user_column(local_mode):
    """本地模式保持旧 schema：落盘的表头不含 user 列（单机模式无用户概念）。"""
    storage.append_row("transactions", "alice", TX)
    path = local_mode / "transactions.csv"
    rows = _rows(path)
    assert len(rows) == 1
    assert "user" not in rows[0]
    assert rows[0]["date"] == "2026-08-20" and rows[0]["amount_rmb"] == "1234.5"


def test_append_rejects_same_day_same_asset_same_action(local_mode):
    """同日同资产同方向再写 → ValueError，且账本不得被追加。"""
    storage.append_row("transactions", "alice", TX)
    with pytest.raises(ValueError, match="重复记录"):
        storage.append_row("transactions", "alice", dict(TX, amount_rmb="9999"))
    assert len(_rows(local_mode / "transactions.csv")) == 1


def test_append_dup_message_quotes_existing_amount(local_mode):
    """报错要说清"已有的那笔是多少钱"，否则用户没法判断该不该覆盖。"""
    storage.append_row("transactions", "alice", TX)
    with pytest.raises(ValueError) as ei:
        storage.append_row("transactions", "alice", TX)
    msg = str(ei.value)
    assert "2026-08-20" in msg and "sp500" in msg and "1234.5" in msg


def test_append_force_bypasses_dedup(local_mode):
    """确认过是新一笔 → force=True 放行，两行都在。"""
    storage.append_row("transactions", "alice", TX)
    storage.append_row("transactions", "alice", dict(TX, amount_rmb="2000"), force=True)
    rows = _rows(local_mode / "transactions.csv")
    assert [r["amount_rmb"] for r in rows] == ["1234.5", "2000"]


def test_append_allows_same_day_opposite_action(local_mode):
    """同日先买后卖是正常序列，不能拦（去重只看"同方向"）。"""
    storage.append_row("transactions", "alice", TX)
    storage.append_row("transactions", "alice", dict(TX, action="sell"))
    assert len(_rows(local_mode / "transactions.csv")) == 2


def test_append_allows_same_day_other_asset(local_mode):
    """同日买不同资产是每天的常态，不能拦。"""
    storage.append_row("transactions", "alice", TX)
    storage.append_row("transactions", "alice", dict(TX, asset="gold", symbol="XAUT"))
    assert len(_rows(local_mode / "transactions.csv")) == 2


def test_append_allows_same_asset_other_day(local_mode):
    """隔天再买同一个资产是定投本身，不能拦。"""
    storage.append_row("transactions", "alice", TX)
    storage.append_row("transactions", "alice", dict(TX, date="2026-08-21"))
    assert len(_rows(local_mode / "transactions.csv")) == 2


def test_observations_are_not_deduped(local_mode):
    """跳过/观察记录不去重——同一天可以记多条观察，去重只针对成交。"""
    obs = {"date": "2026-08-20", "action": "skip", "reason": "虚构", "notes": ""}
    storage.append_row("observations", "alice", obs)
    storage.append_row("observations", "alice", obs)
    assert len(_rows(local_mode / "observations.csv")) == 2


def test_read_rows_empty_when_no_file(local_mode):
    """没有账本文件 → 空列表（不是抛错，新用户就是这个状态）。"""
    assert storage.read_rows("transactions", "alice") == []
    (local_mode / "transactions.csv").write_text("", encoding="utf-8")
    assert storage.read_rows("transactions", "alice") == []


# ============================================================
# 2. PIN 哈希（PBKDF2 + 每账号随机盐）
# ============================================================

def test_pbkdf2_iterations_are_not_silently_lowered():
    """迭代次数是抗离线穷举的全部本钱，被人"为了快点"改小必须立刻失败。"""
    assert storage._PBKDF2_ITER == 200_000


def test_pin_hash_v2_is_deterministic():
    """同 PIN 同盐必须得同一个哈希，否则登录永远失败。"""
    h1 = storage._pin_hash_v2("123456", "a" * 32)
    h2 = storage._pin_hash_v2("123456", "a" * 32)
    assert h1 == h2
    assert len(h1) == 64  # sha256 → 32 字节 → 64 hex


def test_pin_hash_v2_salt_changes_hash():
    """同 PIN 不同盐 → 不同哈希：这是"一张彩虹表打穿所有账号"的防线。"""
    assert storage._pin_hash_v2("123456", "a" * 32) != storage._pin_hash_v2("123456", "b" * 32)


def test_pin_hash_v2_pin_changes_hash():
    assert storage._pin_hash_v2("123456", "a" * 32) != storage._pin_hash_v2("123457", "a" * 32)


def test_new_pin_record_shape_and_verifiability():
    """新账号记录三字段齐全，且能用记下的盐把 PIN 验回来。"""
    rec = storage._new_pin_record("654321")
    assert set(rec) == {"pin_hash", "salt", "hash_algo"}
    assert rec["hash_algo"] == "pbkdf2_v1"
    assert len(rec["salt"]) == 32  # token_hex(16)
    bytes.fromhex(rec["salt"])  # 必须是合法 hex，否则 bytes.fromhex 在登录时炸
    assert storage._pin_hash_v2("654321", rec["salt"]) == rec["pin_hash"]
    assert storage._pin_hash_v2("000000", rec["salt"]) != rec["pin_hash"]


def test_new_pin_record_salt_is_random_per_account():
    """同一个 PIN 的两个账号必须拿到不同盐、不同哈希。"""
    a, b = storage._new_pin_record("123456"), storage._new_pin_record("123456")
    assert a["salt"] != b["salt"]
    assert a["pin_hash"] != b["pin_hash"]


def test_legacy_and_v2_hashes_are_distinct():
    """旧 sha256 与新 PBKDF2 结果不同，两套算法不会互相误判通过。"""
    legacy = storage._pin_hash("alice", "123456")
    assert legacy != storage._pin_hash_v2("123456", "a" * 32)
    assert len(legacy) == 64


# ============================================================
# 3. 读失败拒写（整表覆写模型的命门）
# ============================================================

def test_read_ws_raises_on_read_failure(sheets_mode):
    """读故障必须抛 SheetReadError——绝不返回空表（否则上层抄完空书就把原书覆盖了）。"""
    sheets_mode(read_exc=RuntimeError("network down"))
    with pytest.raises(storage.SheetReadError):
        storage._read_ws("transactions", storage.TX_FIELDS)


def test_read_ws_missing_worksheet_is_empty_not_error(sheets_mode):
    """表还没建 ≠ 读失败：返回带表头的空表，让首次写入能进行。"""
    sheets_mode(read_exc=WorksheetNotFound("no such ws"))
    df = storage._read_ws("transactions", storage.TX_FIELDS)
    assert df.empty and list(df.columns) == storage.TX_FIELDS


def test_append_row_aborts_when_read_fails(sheets_mode):
    """云端读失败时 append_row 必须整体失败，且一次写都不能发出。"""
    conn = sheets_mode(read_exc=RuntimeError("quota exceeded"))
    with pytest.raises(storage.SheetReadError):
        storage.append_row("transactions", "alice", TX)
    assert conn.updates == [] and conn.creates == []


def test_append_row_force_also_aborts_when_read_fails(sheets_mode):
    """force=True 跳过的是去重检查，不是读失败保护——照样不许写。"""
    conn = sheets_mode(read_exc=RuntimeError("quota exceeded"))
    with pytest.raises(storage.SheetReadError):
        storage.append_row("transactions", "alice", TX, force=True)
    assert conn.updates == [] and conn.creates == []


def test_write_ws_aborts_when_pre_read_fails(sheets_mode):
    """写前读不出现内容 → SheetWriteError，宁可不写也不无备份覆写。"""
    conn = sheets_mode(read_exc=RuntimeError("read broke"))
    with pytest.raises(storage.SheetWriteError):
        storage._write_ws("transactions", pd.DataFrame(columns=storage.TX_FIELDS))
    assert conn.updates == [] and conn.creates == []


def test_write_ws_aborts_when_snapshot_fails(sheets_mode):
    """快照 _bak 失败 → 放弃写入正表（fail-closed），正表必须一个字都没动。"""
    existing = pd.DataFrame([dict.fromkeys(storage.TX_FIELDS, "x")])
    conn = sheets_mode(read_result=existing, fail_update_on={"transactions_bak"})
    with pytest.raises(storage.SheetWriteError, match="快照"):
        storage._write_ws("transactions", existing)
    assert "transactions" not in conn.updates
    assert "transactions" not in conn.creates


def test_write_ws_snapshots_before_overwrite(sheets_mode):
    """正常路径：先写 <表>_bak 快照，再覆写正表——顺序不能反。"""
    existing = pd.DataFrame([dict.fromkeys(storage.TX_FIELDS, "x")])
    conn = sheets_mode(read_result=existing)
    storage._write_ws("transactions", existing)
    assert conn.updates == ["transactions_bak", "transactions"]


def test_write_ws_skips_snapshot_on_first_write(sheets_mode):
    """首次写这张表（还不存在）没有可快照的内容，直接写正表，不该报错。"""
    conn = sheets_mode(read_exc=WorksheetNotFound("first time"))
    storage._write_ws("transactions", pd.DataFrame(columns=storage.TX_FIELDS))
    assert conn.updates == ["transactions"]
    assert not any(w.endswith("_bak") for w in conn.updates + conn.creates)


def test_sheet_cache_is_invalidated_by_write(sheets_mode):
    """写后短缓存必须失效，否则下一次读拿到写前的旧表。"""
    df = pd.DataFrame([dict.fromkeys(storage.TX_FIELDS, "x")])
    conn = sheets_mode(read_result=df)
    storage._read_ws("transactions", storage.TX_FIELDS)
    assert "transactions" in storage._SHEET_CACHE
    storage._write_ws("transactions", df)
    assert "transactions" not in storage._SHEET_CACHE
    assert conn.updates[-1] == "transactions"

"""Mốc dữ liệu (data marker) – Blueprint P9, 8.5, 10.4.

Bên nạp dữ liệu ghi bi_meta.data_version = dấu vân tay NỘI DUNG (nội dung không đổi -> giữ phiên bản cũ)
và bi_meta.data_loaded_at. App đọc mốc tối đa 60 giây/lần; mốc đổi -> xoá cache kết quả và danh mục chatbot.
Phiên bản dùng trong khoá cache = data_version + các nguồn phụ (bản Pro: admin bấm "Làm mới cache").
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime

INTERVAL = 60  # giây giữa hai lần đọc mốc

# (bảng, biểu thức) đưa vào dấu vân tay; bảng không có thì bỏ qua
FINGERPRINT = (
    ("bi_org", "COUNT(*)"), ("bi_sales", "COUNT(*)"), ("bi_sales", "SUM(net_revenue)"), ("bi_sales", "SUM(gross_amount)"),
    ("bi_sales", "SUM(cogs)"), ("bi_sales", "SUM(quantity)"), ("bi_sales", "MAX(date)"),
    ("bi_sales_plan", "COUNT(*)"), ("bi_sales_plan", "SUM(plan_revenue)"),
    ("bi_pnl", "COUNT(*)"), ("bi_pnl", "SUM(amount)"), ("bi_pnl_lines", "COUNT(*)"),
    ("bi_cost_detail", "COUNT(*)"), ("bi_cost_detail", "SUM(amount)"),
    ("bi_bs", "COUNT(*)"), ("bi_bs", "SUM(amount)"), ("bi_bs_lines", "COUNT(*)"),
    ("bi_cashflow", "COUNT(*)"), ("bi_cashflow", "SUM(amount)"), ("bi_cf_lines", "COUNT(*)"),
    ("bi_inventory", "COUNT(*)"), ("bi_inventory", "SUM(value_end)"),
)
META_KEYS = ("data_version", "data_loaded_at")


def _norm(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        return f"{float(v):.0f}"
    return str(v)


def fingerprint(scalar: Callable[[str], object]) -> str:
    """scalar(sql) -> giá trị đơn; ném lỗi nếu bảng không có. Dùng chung cho SQLite, PostgreSQL, SQLAlchemy."""
    parts = []
    for table, expr in FINGERPRINT:
        try:
            parts.append(f"{table}.{expr}={_norm(scalar(f'SELECT {expr} FROM {table}'))}")  # noqa: S608 – hằng số
        except Exception:  # noqa: BLE001 – kho chưa có bảng / view này
            parts.append(f"{table}.{expr}=NA")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def stamp(scalar: Callable[[str], object], old_version: str | None) -> dict[str, str] | None:
    """Giá trị cần ghi vào bi_meta sau một lần nạp; None = nội dung không đổi (giữ mốc cũ)."""
    v = fingerprint(scalar)
    if v == old_version:
        return None
    return {"data_version": v, "data_loaded_at": datetime.now().astimezone().isoformat(timespec="seconds")}


# ------------------------------------------------------------------ phía app
extra_sources: list[Callable[[], str]] = []   # bản Pro đăng ký cache_epoch (sec.app_settings)
_lock = threading.Lock()
_state: dict = {"data": None, "extra": None, "t_data": 0.0, "t_extra": 0.0, "loaded_at": None, "changed_at": None}


def _sqlite_mtime(url: str) -> str:
    path = url.removeprefix("sqlite:///").removeprefix("file:").split("?")[0]
    return f"mtime:{os.path.getmtime(path):.0f}" if os.path.exists(path) else "0"


def _read_data() -> tuple[str, str | None]:
    from app.core import dataset, db
    rows = {r["key"]: r["value"] for r in
            db.fetch("SELECT key, value FROM bi_meta WHERE key IN ('data_version', 'data_loaded_at')")}
    v = rows.get("data_version")
    if not v:
        url = dataset.database_url()
        v = _sqlite_mtime(url) if url.startswith("sqlite:///") else "0"
    return v, rows.get("data_loaded_at")


def _read_extra() -> str:
    out = []
    for f in extra_sources:
        try:
            out.append(f() or "")
        except Exception:  # noqa: BLE001 – nguồn phụ lỗi không được làm hỏng dashboard
            out.append("?")
    return ".".join(out)


def clear_derived() -> None:
    """Xoá mọi cache đọc dữ liệu (không đóng kết nối)."""
    from app.chat import nlu
    from app.core import cache, dataset, db, semantic
    for f in (db.meta, semantic.catalog, dataset._available, nlu._values):
        f.cache_clear()
    cache.cache.clear()


def _refresh(part: str) -> str:
    t = "t_" + part
    if _state[part] is not None and time.monotonic() - _state[t] < INTERVAL:
        return _state[part]
    with _lock:
        if _state[part] is not None and time.monotonic() - _state[t] < INTERVAL:
            return _state[part]
        if part == "data":
            v, loaded = _read_data()
            _state["loaded_at"] = loaded
        else:
            v = _read_extra()
        if _state[part] is not None and v != _state[part]:
            clear_derived()
            _state["changed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _state[part], _state[t] = v, time.monotonic()
        return v


def data_version() -> str:
    """Chỉ mốc dữ liệu (bi_meta) – an toàn khi gọi bên trong giao dịch của CSDL sec."""
    return _refresh("data")


def version() -> str:
    """Phiên bản cho khoá cache = mốc dữ liệu + nguồn phụ (đọc lại tối đa 60 giây/lần)."""
    d = _refresh("data")
    x = _refresh("extra") if extra_sources else ""
    return f"{d}.{x}" if x else d


def reset() -> None:
    """Buộc đọc lại mốc ở lần gọi kế tiếp (sau khi đổi kho / admin làm mới)."""
    with _lock:
        _state.update(data=None, extra=None, t_data=0.0, t_extra=0.0)


def info() -> dict:
    v = version()
    return {"version": v, "data_loaded_at": _state["loaded_at"], "changed_at": _state["changed_at"],
            "check_interval_s": INTERVAL}

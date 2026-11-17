"""Điểm truy cập SQL duy nhất (chỉ đọc). Đổi DATABASE_URL để chạy trên SQL Server / PostgreSQL / MySQL."""
from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy import create_engine, text

from app import config

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def engine():
    from app.core import dataset   # khách nạp dữ liệu của mình trên máy -> đọc CSDL đó (app/core/dataset.py)
    url = dataset.database_url()
    for prefix in ("postgres://", "postgresql://"):  # Neon cấp URL không ghi driver
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url.removeprefix(prefix)
    return create_engine(url, pool_pre_ping=True, pool_recycle=300, future=True)


def fetch(sql: str, params: dict | None = None) -> list[dict]:
    with engine().connect() as con:
        res = con.execute(text(sql), params or {})
        rows = res.mappings().fetchmany(config.SQL_ROW_LIMIT)
        return [dict(r) for r in rows]


@lru_cache(maxsize=1)
def meta() -> dict:
    out = {r["key"]: r["value"] for r in fetch("SELECT key, value FROM bi_meta")}
    if config.COMPANY_NAME:
        out["company_name"] = config.COMPANY_NAME
    return out

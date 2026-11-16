"""Kết nối SQLite ở chế độ CHỈ ĐỌC – người dùng demo không thể sửa/xóa dữ liệu mẫu."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(
                f"Không tìm thấy database mẫu: {path}. Chạy: python tools/generate_sample_data.py")
        self.path = path
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
            con.execute("PRAGMA query_only = ON")
            self._local.con = con
        return con

    def query(self, sql: str, params: dict | tuple = ()) -> tuple[list[str], list[tuple]]:
        cur = self._conn().execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, cur.fetchall()

    def meta(self) -> dict[str, str]:
        _, rows = self.query("SELECT Khoa, GiaTri FROM Meta_DuLieu")
        return dict(rows)

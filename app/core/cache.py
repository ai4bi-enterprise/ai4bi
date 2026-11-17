"""Cache kết quả dùng chung cho dashboard và chatbot (Blueprint P9)."""
from __future__ import annotations

import os
import time
from collections import OrderedDict

from app import config
from app.core import db


class ResultCache:
    """Khoá = endpoint + tham số SAU khi áp phạm vi + phiên bản dữ liệu. LRU, TTL."""

    def __init__(self, size: int = 256, ttl: int = 600):
        self.size, self.ttl, self.data = size, ttl, OrderedDict()

    @staticmethod
    def version() -> str:
        url = config.DATABASE_URL
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            return str(os.path.getmtime(path)) if os.path.exists(path) else "0"
        return db.meta().get("data_version", "0")

    def get(self, key, build):
        key = (*key, self.version())
        hit = self.data.get(key)
        if hit and time.time() - hit[0] < self.ttl:
            self.data.move_to_end(key)
            return hit[1]
        val = build()
        self.data[key] = (time.time(), val)
        if len(self.data) > self.size:
            self.data.popitem(last=False)
        return val


cache = ResultCache()

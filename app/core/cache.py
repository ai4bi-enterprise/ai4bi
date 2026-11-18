"""Cache kết quả dùng chung cho dashboard và chatbot (Blueprint P9, 10.4).

Khoá = endpoint + tham số SAU khi áp phạm vi + phiên bản dữ liệu (mốc, app/core/marker.py). LRU 512, TTL 600 giây.
Cache trống / hết hạn: chỉ một request chạy SQL cho mỗi khoá, các request khác chờ và dùng lại kết quả.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from app.core import marker


class ResultCache:
    def __init__(self, size: int = 512, ttl: int = 600):
        self.size, self.ttl, self.data = size, ttl, OrderedDict()
        self._lock = threading.Lock()
        self._building: dict = {}

    @staticmethod
    def version() -> str:
        return marker.version()

    def _fresh(self, key):
        hit = self.data.get(key)
        if hit and time.time() - hit[0] < self.ttl:
            self.data.move_to_end(key)
            return hit
        return None

    def get(self, key, build):
        key = (*key, self.version())
        with self._lock:
            hit = self._fresh(key)
            if hit:
                return hit[1]
            lk = self._building.setdefault(key, threading.Lock())
        with lk:  # [single-flight-v1]
            with self._lock:
                hit = self._fresh(key)
            if hit:
                return hit[1]
            try:
                val = build()
                with self._lock:
                    self.data[key] = (time.time(), val)
                    self.data.move_to_end(key)
                    while len(self.data) > self.size:
                        self.data.popitem(last=False)
                return val
            finally:
                with self._lock:
                    if self._building.get(key) is lk:
                        del self._building[key]

    def clear(self) -> None:
        with self._lock:
            self.data.clear()


cache = ResultCache()

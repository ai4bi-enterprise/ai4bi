"""Bản Lite: mốc dữ liệu + cache theo phiên bản, không có CSDL sec / nút admin. Chạy: pytest -q"""
from app.core import cache, marker


def test_marker_without_sec_sources():
    assert marker.extra_sources == []
    v = marker.version()
    assert v and "." not in v and marker.data_version() == v
    calls = []
    assert cache.cache.get(("lite-probe",), lambda: calls.append(1) or 7) == 7
    assert cache.cache.get(("lite-probe",), lambda: calls.append(1) or 8) == 7 and len(calls) == 1

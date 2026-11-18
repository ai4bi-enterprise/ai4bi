"""Bản Lite: bộ golden chạy bằng parser luật; chỉ tiêu phân hệ Pro không được trả số; không lộ phạm vi. Chạy: pytest -q"""
from app.core import golden


def test_golden_lite_rules_not_below_baseline():
    sc = golden.score(golden.run())
    base = golden.load()["baseline"]["lite_rules"]
    assert sc["scope_leaks"] == 0, sc["failed"]
    assert sc["number"] >= base["number"] and sc["intent"] >= base["intent"], sc

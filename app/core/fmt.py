"""Định dạng số kiểu Việt Nam – một nguồn duy nhất cho dashboard và chatbot (Blueprint P1)."""
from __future__ import annotations


def _num(v: float, digits: int) -> str:
    s = f"{v:,.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def money(v: float | None) -> str:
    if v is None:
        return "–"
    a = abs(v)
    if a >= 1e12:
        return f"{_num(v / 1e12, 2)} nghìn tỷ"
    if a >= 1e9:
        return f"{_num(v / 1e9, 1 if a >= 1e11 else 2)} tỷ"
    if a >= 1e6:
        return f"{_num(v / 1e6, 1)} triệu"
    return f"{_num(v, 0)} đ"


def pct(v: float | None, digits: int = 1) -> str:
    return "–" if v is None else f"{_num(v * 100, digits)}%"


def pp(v: float | None) -> str:
    """Chênh lệch tỷ lệ: điểm %."""
    return "–" if v is None else f"{'+' if v >= 0 else ''}{_num(v * 100, 1)} điểm %"


def number(v: float | None) -> str:
    return "–" if v is None else _num(v, 0)


def value(v: float | None, kind: str) -> str:
    if v is None:
        return "–"
    return {
        "money": money, "pct": pct, "number": number,
        "days": lambda x: f"{_num(x, 0)} ngày",
        "times": lambda x: f"{_num(x, 2)} lần",
    }.get(kind, number)(v)


def growth(cur: float | None, base: float | None) -> float | None:
    if cur is None or base in (None, 0):
        return None
    return (cur - base) / abs(base)


def signed_pct(v: float | None) -> str:
    return "–" if v is None else f"{'+' if v >= 0 else ''}{_num(v * 100, 1)}%"

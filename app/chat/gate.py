"""Cổng kiểm lời văn LLM bằng code (Blueprint 12.4: G1 số ngoài dữ liệu, G2 lộ mã tạm, G4 tăng/giảm khớp dấu)."""
from __future__ import annotations

import re

NUM = re.compile(r"[+\-−]?\d[\d.]*(?:,\d+)?")
ALIAS = re.compile(r"\bKH_\d+\b")


def _canon(s: str) -> str:
    return s.replace("−", "-").lstrip("+").rstrip(".,")


def check(text: str, evidence_text: str, period_words: str = "") -> list[str]:
    errors = []
    allowed = {_canon(m) for m in NUM.findall(evidence_text + " " + period_words)}
    allowed |= {_canon(m).lstrip("-") for m in allowed}
    for m in NUM.findall(text):
        c = _canon(m)
        if c.lstrip("-") in allowed or c in allowed:
            continue
        if re.fullmatch(r"\d{1,2}", c.lstrip("-")) or re.fullmatch(r"20\d\d", c):
            continue  # tháng, quý, năm, số thứ tự
        errors.append(f"G1: số '{m}' không có trong dữ liệu")
    if ALIAS.search(text):
        errors.append("G2: lộ mã tạm")
    for word, sign in (("tăng", "-"), ("giảm", "+")):
        for mm in re.finditer(rf"{word}\s+([+\-−]\d[\d.,]*%)", text):
            if mm.group(1).startswith(sign if sign == "+" else ("-", "−")):
                errors.append(f"G4: '{mm.group(0)}' ngược dấu")
    return errors

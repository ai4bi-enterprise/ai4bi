"""Chuẩn hóa tiếng Việt để so khớp (bỏ dấu, chữ thường, gộp khoảng trắng)."""
from __future__ import annotations

import re
import unicodedata


def normalize(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9%>&\s\-]", " ", s)
    s = s.replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def pattern_for(syn: str) -> re.Pattern:
    """Biên dịch từ đồng nghĩa: 're:...' là regex, còn lại khớp nguyên cụm từ."""
    if syn.startswith("re:"):
        return re.compile(r"(?<![a-z0-9])" + syn[3:] + r"(?![a-z0-9])")
    return re.compile(r"(?<![a-z0-9])" + re.escape(normalize(syn)) + r"(?![a-z0-9])")

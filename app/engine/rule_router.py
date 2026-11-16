"""
Bộ định tuyến theo luật (Rule Router) – chạy KHÔNG cần API key.

Biến câu hỏi tiếng Việt thành Intent JSON bằng cách so khớp từ đồng nghĩa trong
Semantic Catalog. Khi có LLM, LLM Router được ưu tiên và Rule Router làm dự phòng.
"""
from __future__ import annotations

import re

from .catalog import Catalog
from .text import normalize, pattern_for

BOD_PATTERNS = [
    r"^(tai sao|vi sao|nguyen nhan|giai thich|phan tich nguyen nhan|ly do)\b",
    r"\b(tai sao|vi sao|nguyen nhan|bao mon|an mon|root cause|canh bao)\b",
]
TIME_GROUP = {
    "quy": [r"theo quy", r"tung quy", r"cac quy", r"hang quy", r"moi quy", r"4 quy", r"bon quy"],
    "thang": [r"theo thang", r"tung thang", r"cac thang", r"hang thang", r"moi thang", r"xu huong", r"12 thang"],
}
COMPARE = [r"so voi (quy|thang|ky) truoc", r"\bqoq\b", r"\bmom\b", r"tang truong", r"bien dong",
           r"so voi cung ky", r"tang hay giam", r"tang giam"]
DESC = [r"cao nhat", r"lon nhat", r"nhieu nhat", r"dan dau", r"\btop\b", r"tot nhat"]
ASC = [r"thap nhat", r"it nhat", r"nho nhat", r"kem nhat", r"te nhat"]


def is_bod_question(q: str) -> bool:
    n = normalize(q)
    return any(re.search(p, n) for p in BOD_PATTERNS)


class RuleRouter:
    def __init__(self, catalog: Catalog):
        self.cat = catalog
        self._values = catalog.value_patterns()
        self._metric_pats = sorted(
            [(mk, pattern_for(s), len(s)) for mk, m in catalog.metrics.items() for s in m.synonyms],
            key=lambda x: -x[2])
        self._dim_pats = sorted(
            [(dk, pattern_for(s), len(s)) for dk, d in catalog.dimensions.items() for s in d.synonyms
             if d.kind != "time"], key=lambda x: -x[2])

    def parse(self, question: str) -> dict:
        text = " " + normalize(question) + " "
        intent: dict = {"metrics": [], "dimensions": [], "filters": {}, "quarters": [], "months": [],
                        "year": None, "compare": None, "order": None, "limit": None}

        def mask(m: re.Match):
            nonlocal text
            text = text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]

        # --- Năm / Quý / Tháng
        for m in list(re.finditer(r"\b(20\d{2})\b", text)):
            intent["year"] = int(m.group(1)); mask(m)
        if re.search(r"(6|sau) thang dau nam|nua dau nam|\bh1\b", text):
            intent["quarters"] = [1, 2]
        if re.search(r"(6|sau) thang cuoi nam|nua cuoi nam|\bh2\b", text):
            intent["quarters"] = [3, 4]
        for m in list(re.finditer(r"\b(?:quy|q)\s*([1-4])\b", text)):
            intent["quarters"].append(int(m.group(1))); mask(m)
        for m in list(re.finditer(r"\b(?:va|,)\s*([1-4])\b(?=.*)", text)):
            if intent["quarters"]:
                intent["quarters"].append(int(m.group(1))); mask(m)
        for m in list(re.finditer(r"\bthang\s*(1[0-2]|[1-9])\b(?!\s*(dau|cuoi))", text)):
            intent["months"].append(int(m.group(1))); mask(m)

        # --- Top N / thứ tự
        m = re.search(r"\btop\s*(\d{1,3})\b", text)
        if m:
            intent["limit"] = int(m.group(1)); intent["order"] = "desc"; mask(m)
        if any(re.search(p, text) for p in ASC):
            intent["order"] = "asc"
        elif any(re.search(p, text) for p in DESC):
            intent["order"] = intent["order"] or "desc"
        if not intent["limit"]:
            m = re.search(r"\b(\d{1,2})\s+(khach hang|san pham|mat hang|dai ly|chi nhanh|nhom hang)", text)
            if m and intent["order"]:
                intent["limit"] = int(m.group(1))

        # --- So sánh kỳ trước
        if any(re.search(p, text) for p in COMPARE):
            intent["compare"] = "prev"

        # --- Gom theo thời gian
        for dk, pats in TIME_GROUP.items():
            if any(re.search(p, text) for p in pats) and dk not in intent["dimensions"]:
                intent["dimensions"].append(dk)

        # --- Giá trị lọc (dài trước, che phần đã khớp)
        # Bỏ qua giá trị nằm trọn trong một cụm chỉ số (vd "nợ quá hạn >90 ngày" là chỉ số,
        # không phải bộ lọc nhóm tuổi nợ "Quá hạn >90 ngày").
        metric_spans = [(mt.start(), mt.end()) for _, pat, _ in self._metric_pats for mt in pat.finditer(text)]
        for dim, vals, pat, _ in self._values:
            for mt in list(pat.finditer(text)):
                if any(a <= mt.start() and mt.end() <= b for a, b in metric_spans):
                    continue
                lst = intent["filters"].setdefault(dim, [])
                for v in vals:
                    if v not in lst:
                        lst.append(v)
                mask(mt)

        # --- Chỉ số (bỏ qua cụm nằm trọn trong tên chiều, vd "chi phí" trong "loại chi phí")
        dim_spans = [(mt.start(), mt.end()) for _, pat, _ in self._dim_pats for mt in pat.finditer(text)]
        for mk, pat, _ in self._metric_pats:
            for mt in list(pat.finditer(text)):
                if any(a <= mt.start() and mt.end() <= b and (b - a) > (mt.end() - mt.start()) for a, b in dim_spans):
                    continue
                if mk not in intent["metrics"]:
                    intent["metrics"].append(mk)
                mask(mt)
        # giữ thứ tự xuất hiện trong câu
        norm_q = normalize(question)
        intent["metrics"].sort(key=lambda k: min(
            [mm.start() for s in self.cat.metrics[k].synonyms for mm in pattern_for(s).finditer(norm_q)] or [999]))

        # --- Chiều phân tích
        for dk, pat, _ in self._dim_pats:
            if pat.search(text):
                if dk not in intent["dimensions"] and len(intent["filters"].get(dk, [])) != 1:
                    intent["dimensions"].append(dk)
                for mt in list(pat.finditer(text)):
                    mask(mt)
        # Lọc nhiều giá trị của cùng chiều mà không gom chiều khác -> hiển thị theo chiều đó
        for dk, vals in intent["filters"].items():
            if len(vals) > 1 and dk not in intent["dimensions"] and not intent["dimensions"]:
                intent["dimensions"].append(dk)
        if not intent["metrics"] and "nhom_tuoi_no" in intent["dimensions"]:
            intent["metrics"] = ["du_no"]
        if len(intent["quarters"]) > 1 and not intent["dimensions"]:
            intent["dimensions"].append("quy")
        intent["quarters"] = sorted(set(intent["quarters"]))
        intent["months"] = sorted(set(intent["months"]))
        return intent

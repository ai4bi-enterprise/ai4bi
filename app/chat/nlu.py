"""Hiểu câu hỏi tiếng Việt bằng luật -> phiếu câu hỏi (QForm).

Dùng khi không có LLM, hoặc làm phương án dự phòng khi LLM lỗi. Mọi tên đều lấy từ lớp ngữ nghĩa
(metrics.yaml, dimensions.yaml, KEYWORDS của module) nên thêm chỉ tiêu mới không phải sửa file này.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from app.core.semantic import catalog, model

MAX_PARTS = 3


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w%/&\- ]", " ", s)).strip()


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


@lru_cache(maxsize=1)
def _metric_phrases() -> list[tuple[str, str]]:
    out = []
    for k, m in model()["metrics"].items():
        if m.get("internal"):
            continue
        for s in [m["label"], *m.get("synonyms", [])]:
            out.append((norm(s), k))
    return sorted(set(out), key=lambda x: -len(x[0]))


@lru_cache(maxsize=1)
def _dim_phrases() -> list[tuple[str, str]]:
    out = []
    for k, d in model()["dims"].items():
        for s in ([] if d.get("time") else [d["label"]]) + d.get("synonyms", []):
            out.append((norm(s), k))
    return sorted(set(out), key=lambda x: -len(x[0]))


@lru_cache(maxsize=1)
def _values() -> list[tuple[str, str, list[str]]]:
    """(cụm từ, chiều, giá trị lọc) – gồm tên đầy đủ và tên rút gọn sau dấu '–'."""
    out = []
    cat = catalog()
    for d, vals in cat.items():
        for v in vals:
            out.append((norm(v), d, [v]))
            if "–" in v:
                out.append((norm(v.split("–")[-1]), d, [v]))
    us = [v for v in cat.get("org", []) if v.startswith("Hoa Kỳ")]
    for alias in ("hoa ky", "my"):
        out.append((alias, "org", us))
    return sorted(out, key=lambda x: -len(x[0]))


@lru_cache(maxsize=1)
def _blocks() -> list[tuple[str, str]]:
    from app.modules import REGISTRY
    out = []
    for mod, m in REGISTRY.items():
        for bid, kws in getattr(m, "KEYWORDS", {}).items():
            for kw in kws:
                out.append((norm(kw), f"{mod}.{bid}"))
    return sorted(out, key=lambda x: -len(x[0]))


def _take(text: str, phrases: list[tuple[str, str]]) -> tuple[list[str], str]:
    """Bắt cụm dài nhất trước, xoá khỏi câu để cụm ngắn hơn không bắt trùng."""
    found = []
    for p, k in phrases:
        if p and _has(text, p):
            if k not in found:
                found.append(k)
            text = re.sub(rf"(?<!\w){re.escape(p)}(?!\w)", " ", text)
    return found, text


ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


def parse_period(t: str, ctx: dict) -> dict:
    """Trả {year, month, mode}; thiếu thì lấy từ ngữ cảnh màn hình đang xem."""
    # [cmp-period-v1] "so với (cùng kỳ) năm trước / năm ngoái" là vế so sánh, không phải kỳ hỏi
    t = re.sub(r"\b(?:so voi|cung ky)(?: cung ky)?(?: cua)? nam (?:truoc|ngoai)\b", " cung ky ", t)
    # [q-short-v1] "Q3", "Q3/2025", "Q3-2025", "2025 Q3" = quý
    t = re.sub(r"\b(20\d\d)\s*q\s?([1-4])\b", r"quy \2/\1", t)
    t = re.sub(r"\bq\s?([1-4])\b", r"quy \1", t)
    year, month, mode = ctx.get("year"), ctx.get("month"), ctx.get("mode", "month")
    latest_y, latest_m = ctx.get("latest_year", year), ctx.get("latest_month", month)
    explicit = False
    if m := re.search(r"\bnam (20\d\d)\b", t):
        year, explicit = int(m.group(1)), True
        mode, month = "ytd", (12 if int(m.group(1)) < latest_y else latest_m)
    if _has(t, "nam nay"):
        year, month, mode, explicit = latest_y, latest_m, "ytd", True
    if _has(t, "nam truoc") or _has(t, "nam ngoai"):
        year, month, mode, explicit = latest_y - 1, 12, "ytd", True
    if m := re.search(r"\bquy (\d|iv|iii|ii|i)\b(?:\s*(?:/|-|nam)?\s*(20\d\d))?", t):
        q = int(m.group(1)) if m.group(1).isdigit() else ROMAN[m.group(1)]
        year = int(m.group(2)) if m.group(2) else year
        month, mode, explicit = q * 3, "quarter", True
        if year == latest_y and month > latest_m:
            month = latest_m
    if _has(t, "quy nay"):
        year, month, mode, explicit = latest_y, latest_m, "quarter", True
    if _has(t, "quy truoc"):
        q = (latest_m - 1) // 3
        year, month, mode, explicit = (latest_y, q * 3, "quarter", True) if q else (latest_y - 1, 12, "quarter", True)
    if m := re.search(r"\bthang (\d{1,2})(?:\s*(?:/|nam)\s*(20\d\d))?\b", t):
        month, mode, explicit = int(m.group(1)), "month", True
        year = int(m.group(2)) if m.group(2) else (year if month <= latest_m or year != latest_y else latest_y - 1)
    if _has(t, "thang nay"):
        year, month, mode, explicit = latest_y, latest_m, "month", True
    if _has(t, "thang truoc") and not _has(t, "so voi thang truoc"):
        year, month = (latest_y, latest_m - 1) if latest_m > 1 else (latest_y - 1, 12)
        mode, explicit = "month", True
    if any(_has(t, p) for p in ("luy ke", "ytd", "tu dau nam")):
        mode, explicit = "ytd", True
    return {"year": year, "month": month, "mode": mode, "explicit": explicit}


def parse_one(question: str, ctx: dict) -> dict:
    t = norm(question)
    q: dict = {"question": question, "intent": "value", "metrics": [], "dims": [], "filters": {}, "compare": [],
               "top_n": None, "order": "desc", "block": None}
    if re.fullmatch(r"(xin )?chao( ban)?|hello|hi|alo", t):
        q["intent"] = "greeting"
        return q
    if any(_has(t, p) for p in ("ban la ai", "ban lam duoc gi", "huong dan", "giup gi", "hoi gi")):
        q["intent"] = "help"
        return q
    q["period"] = parse_period(t, ctx)
    blocks, _ = _take(t, _blocks())
    rest = t
    for p, d, vals in _values():
        if _has(rest, p):
            q["filters"].setdefault(d, [])
            q["filters"][d] += [v for v in vals if v not in q["filters"][d]]
            rest = re.sub(rf"(?<!\w){re.escape(p)}(?!\w)", " ", rest)
    metrics, rest = _take(rest, _metric_phrases())
    after_metrics = rest   # [trend-rest-v1] "số đơn hàng tháng 9" không phải "hàng tháng"
    dims, rest = _take(rest, _dim_phrases())
    q["metrics"] = metrics[:3]
    # chiều nào đã là bộ lọc 1 giá trị thì không tách theo chiều đó nữa
    q["dims"] = [d for d in dims if not (d in q["filters"] and len(q["filters"][d]) == 1) and d != "month"][:1]
    if any(_has(t, p) for p in ("vi sao", "tai sao", "nguyen nhan", "ly do", "do dau", "yeu to nao", "giai thich")):
        q["intent"] = "explain"
    elif "month" in dims or any(_has(after_metrics, p) for p in ("xu huong", "bien dong", "dien bien", "theo thang", "tung thang",
                                                      "hang thang", "12 thang")):
        q["intent"] = "trend"
    elif m := re.search(r"\btop ?(\d+)\b", t):
        q["intent"], q["top_n"] = "rank", int(m.group(1))
    elif any(_has(t, p) for p in ("cao nhat", "lon nhat", "nhieu nhat", "tot nhat", "manh nhat")):
        q["intent"], q["top_n"] = "rank", 5
    elif any(_has(t, p) for p in ("thap nhat", "it nhat", "kem nhat", "nho nhat", "yeu nhat")):
        q["intent"], q["top_n"], q["order"] = "rank", 5, "asc"
    elif q["dims"]:
        q["intent"] = "breakdown"
    if q["intent"] in ("rank", "breakdown") and not q["dims"] and q["filters"].get("channel") == ["Đại lý"]             and any(_has(t, p) for p in ("dai ly nao", "top", "cac dai ly", "tung dai ly", "dai ly lon")):
        q["dims"] = ["customer"]
    if q["intent"] == "rank" and not q["dims"]:
        q["dims"] = [d for d in dims if d != "month"][:1] or ["org"]
    if any(_has(t, p) for p in ("cung ky", "yoy", "so voi nam truoc", "so voi nam ngoai", "tang truong")):
        q["compare"].append("yoy")
    if any(_has(t, p) for p in ("so voi thang truoc", "so voi ky truoc", "so voi quy truoc", "ky truoc")):
        q["compare"].append("prev")
    if any(_has(t, p) for p in ("ke hoach", "kpi", "chi tieu", "target", "aop", "dat bao nhieu")):
        q["compare"].append("plan")
        if "plan_revenue" in q["metrics"] and len(q["metrics"]) == 1:
            q["metrics"] = ["net_revenue"]
    # [filter-value-v1] hỏi giá trị có chỉ tiêu + bộ lọc -> trả giá trị đã lọc, không đổi sang khối dashboard (khối không áp bộ lọc của câu)
    if blocks and q["intent"] == "value" and q["metrics"] and q["filters"]:
        blocks = []
    if blocks and (q["intent"] in ("value", "explain") or not q["metrics"]):
        q["block"] = blocks[0]
        if not q["metrics"] or q["intent"] != "explain":
            q["intent"] = "block"
    if q["intent"] == "explain" and not q["metrics"] and not q["block"]:
        q["metrics"] = ["net_revenue"]
    if not q["metrics"] and not q["block"]:
        q["intent"] = "unknown" if not q["filters"] and not q["period"]["explicit"] else "followup"
    return q


def parse(question: str, ctx: dict) -> list[dict]:
    """Tách câu nhiều ý (tối đa 3) theo 'và' / ';' / '?' khi mỗi vế có chỉ tiêu riêng."""
    pieces = [p for p in re.split(r"[;?]|\s+(?:và|, còn|còn)\s+", question) if p and p.strip()]
    parts = [parse_one(p, ctx) for p in pieces] if len(pieces) > 1 else []
    parts = [p for p in parts if p["metrics"] or p["block"]]
    if len(parts) > 1:
        explicit = [p["period"] for p in parts if p.get("period", {}).get("explicit")]
        for i, p in enumerate(parts):  # vế không nói kỳ -> dùng kỳ được nói gần nhất phía sau, rồi phía trước
            if not p.get("period", {}).get("explicit") and explicit:
                later = [x["period"] for x in parts[i + 1:] if x.get("period", {}).get("explicit")]
                p["period"] = later[0] if later else explicit[-1]
        simple = all(p["intent"] == "value" and not p["block"] for p in parts)
        if simple and len({repr((p["period"], p["filters"])) for p in parts}) == 1:
            merged = dict(parts[0])
            merged["metrics"] = list(dict.fromkeys(m for p in parts for m in p["metrics"]))[:4]
            merged["compare"] = sorted({c for p in parts for c in p["compare"]})
            return [merged]
        return parts[:MAX_PARTS]
    return [parse_one(question, ctx)]

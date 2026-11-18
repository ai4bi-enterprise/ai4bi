"""Bộ golden chatbot – chấm Router + engine bằng đáp án SQL độc lập (Blueprint 12.5, 17 "Chatbot", 24 G4).

File câu hỏi: golden/chat_golden.yaml. Đáp án số KHÔNG ghi cứng: mỗi lần chạy tính lại bằng SQL độc lập
(định nghĩa trong recon.INDEP + EXTRA dưới đây, không đọc metrics.yaml, không đi qua engine).
Mỗi câu chấm 3 tiêu chí: ý (phiếu khớp `expect`), số (lệch trong ngưỡng), phạm vi (không lộ ngoài quyền).
Kết quả không chứa tên khách hàng (chỉ mã câu + mô tả lỗi).
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app import config
from app.core import recon
from app.core.recon import INDEP, num, q
from app.core.scope import User

FILE = Path(config.ROOT) / "golden" / "chat_golden.yaml"
THRESHOLD = 0.95                     # KE_HOACH G4: ≥ 95% câu golden đúng số
TOL_ABS, TOL_REL, TOL_RATIO = 1000.0, 0.001, 0.0005

# Chỉ tiêu ngoài INDEP – định nghĩa độc lập: (bảng, biểu thức tổng hợp, điều kiện, kind)
EXTRA = {
    "quantity": ("bi_sales", "SUM(quantity)", None, "flow"),
    "orders": ("bi_sales", "COUNT(DISTINCT order_no)", None, "flow"),
    "pbt": ("bi_pnl", "SUM(amount)", "scenario = 'actual' AND line_code <> 'THUE_TNDN'", "flow"),
}
SALES_COLS = {"channel", "category", "customer_type"}      # bộ lọc theo cột bi_sales
DIM_COL = {"region": "o.region_name", "org": "o.org_name", "category": "t.category", "channel": "t.channel",
           "customer": "t.customer_name"}

USERS = {  # 3 tài khoản chuẩn của bước 4 (đầy đủ / một khu vực / không phạm vi)
    "full": User("golden_full", "Golden đầy đủ", "BOD", ("*",), None),
    "eu": User("golden_eu", "Golden Châu Âu", "Quản lý khu vực", ("*",), frozenset({"FR", "DE", "UK"})),
    "none": User("golden_none", "Golden không phạm vi", "Khác", ("*",), frozenset()),
}


# ------------------------------------------------------------------ kỳ (tự tính, không dùng app.core.periods)
def months(y: int, m: int, mode: str) -> list[tuple[int, int]]:
    first = m if mode == "month" else ((m - 1) // 3) * 3 + 1 if mode == "quarter" else 1
    return [(y, i) for i in range(first, m + 1)]


def _range(period: list, yoy: bool = False) -> tuple[int, int]:
    y, m, mode = period[0] - (1 if yoy else 0), period[1], period[2] if len(period) > 2 else "month"
    ms = months(y, m, mode)
    return ms[0][0] * 100 + ms[0][1], ms[-1][0] * 100 + ms[-1][1]


# ------------------------------------------------------------------ đáp án SQL độc lập
def _defn(key: str) -> tuple[str, str, str | None, str]:
    if key in EXTRA:
        return EXTRA[key]
    table, expr, cond, kind, _ = INDEP[key]
    return table, f"SUM({expr})", cond, kind


def _codes(chk: dict, user: User) -> list[str] | None:
    codes = None
    if chk.get("orgs"):
        codes = list(chk["orgs"])
    if chk.get("region"):
        codes = [o["org_code"] for o in recon.orgs() if o["region_name"] == chk["region"]]
    if user.orgs is not None:
        codes = sorted(user.orgs if codes is None else set(codes) & user.orgs)
    return codes


def _sql(key: str, rng: tuple[int, int], codes: list[str] | None, filters: dict | None,
         group: str | None = None, order: str | None = None, limit: int | None = None):
    table, agg, cond, kind = _defn(key)
    w = ["t.year*100+t.month = :e" if kind == "snapshot" else "t.year*100+t.month BETWEEN :s AND :e"]
    p = {"s": rng[0], "e": rng[1]}
    if cond:
        w.append(cond)   # chỉ cột của bảng fact (line_code, scenario) – không trùng tên với bi_org
    if codes is not None:
        w.append("t.org_code IN (" + (", ".join(f":o{i}" for i in range(len(codes))) or "NULL") + ")")
        p.update({f"o{i}": c for i, c in enumerate(codes)})
    for col, vals in (filters or {}).items():
        w.append(f"t.{col} IN (" + ", ".join(f":f{col}{i}" for i in range(len(vals))) + ")")
        p.update({f"f{col}{i}": v for i, v in enumerate(vals)})
    join = " LEFT JOIN bi_org o ON o.org_code = t.org_code" if group and group.startswith("o.") else ""
    sel = f"{group} AS k, {agg} AS v" if group else f"{agg} AS v"
    sql = f"SELECT {sel} FROM {table} t{join} WHERE {' AND '.join(w)}"  # noqa: S608 – định nghĩa cố định
    if group:
        sql += f" GROUP BY {group}"
    if order:
        sql += f" ORDER BY v {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = q(sql, p)
    return {r["k"]: num(r["v"]) for r in rows} if group else num(rows[0]["v"])


def expected(chk: dict, user: User, yoy: bool = False):
    codes, flt = _codes(chk, user), chk.get("filters")
    rng = _range(chk["period"], yoy)
    if "ratio" in chk:
        a, b = (_sql(k, rng, codes, flt) for k in chk["ratio"])
        return a / b if b else None
    if chk.get("by"):
        return _sql(chk["metric"], rng, codes, flt, DIM_COL[chk["by"]])
    if chk.get("rank"):
        return _sql(chk["metric"], rng, codes, flt, DIM_COL[chk["rank"]], chk.get("order", "DESC"), chk.get("top", 5))
    if chk.get("trend"):
        y, m, mode = chk["period"]
        return [_sql(chk["metric"], (yy * 100 + mm, yy * 100 + mm), codes, flt) for yy, mm in months(y, m, mode)]
    return _sql(chk["metric"], rng, codes, flt)


# ------------------------------------------------------------------ chấm
@dataclass
class Result:
    id: str
    split: str
    group: str
    user: str
    question: str
    intent_ok: bool = True
    number_ok: bool | None = None      # None = câu không có đáp án số
    scope_ok: bool = True
    errors: list[str] = field(default_factory=list)
    parser: str = ""
    ms: int = 0
    pro_only: bool = False             # bản Lite: câu hỏi chỉ tiêu Pro


def close(a, b, ratio: bool = False) -> bool:
    if a is None or b is None:
        return a is None and b is None
    d = abs(float(a) - float(b))
    return d <= (TOL_RATIO if ratio else TOL_ABS) or d <= TOL_REL * max(abs(float(b)), 1.0)


def _norm_period(p: dict) -> list:
    return [p.get("year"), p.get("month"), p.get("mode")]


def check_intent(exp: dict, parts: list[dict]) -> str | None:
    """None = khớp; trả mô tả lỗi khi không có phần nào khớp phiếu mong đợi."""
    why = []
    for p in parts:
        bad = []
        if exp.get("intent") and p.get("intent") != exp["intent"]:
            bad.append(f"intent {p.get('intent')}≠{exp['intent']}")
        miss = [m for m in exp.get("metrics", []) if m not in (p.get("metrics") or [])]
        if miss:
            bad.append(f"thiếu chỉ tiêu {miss} (có {p.get('metrics')})")
        if "dim" in exp and ((p.get("dims") or [None])[0] != exp["dim"]):
            bad.append(f"chiều {(p.get('dims') or [None])[0]}≠{exp['dim']}")
        if exp.get("period"):
            got = _norm_period(p.get("period") or {})[:len(exp["period"])]
            if got != list(exp["period"]):
                bad.append(f"kỳ {got}≠{exp['period']}")
        for k, vals in (exp.get("filters") or {}).items():
            if sorted((p.get("filters") or {}).get(k) or []) != sorted(vals):
                bad.append(f"lọc {k}={(p.get('filters') or {}).get(k)}≠{vals}")
        if exp.get("compare") and exp["compare"] not in (p.get("compare") or []):
            bad.append(f"thiếu so sánh {exp['compare']}")
        if not bad:
            return None
        why.append("; ".join(bad))
    return " | ".join(why) or "không có phiếu"


def _table_blocks(res: dict):
    for b in res.get("blocks") or []:
        t = b.get("table") or {}
        if t.get("rows"):
            yield t["rows"]


def _has_numbers(res: dict) -> bool:
    if any(k.get("value") is not None for k in res.get("kpis") or []):
        return True
    for b in res.get("blocks") or []:
        if any(v is not None for s in b.get("series") or [] for v in (s.get("data") or [])):
            return True
        if (b.get("table") or {}).get("rows"):
            return True
    return False


def check_number(chk: dict, user: User, res: dict) -> str | None:
    key = chk["metric"]
    ratio = "ratio" in chk
    exp = expected(chk, user)
    if chk.get("by") or chk.get("rank"):
        dim = chk.get("by") or chk.get("rank")
        rows = next((r for r in _table_blocks(res) if dim in r[0] and key in r[0]), None)
        if rows is None:
            return f"không có bảng {key} theo {dim}"
        got = {r[dim]: r[key] for r in rows}
        if chk.get("rank"):
            top = list(exp.items())
            gl = list(got.items())[:len(top)]
            if len(gl) < len(top) or any(not close(gv, ev) for (_, gv), (_, ev) in zip(gl, top)):
                return f"top {len(top)} {key} theo {dim} lệch SQL"
            if [k for k, _ in gl] != [k for k, _ in top]:
                return f"thứ tự top {len(top)} theo {dim} khác SQL"
            return None
        exp = {k: v for k, v in exp.items() if k is not None and v}
        miss = [k for k in exp if k not in got]
        extra = [k for k in got if k not in exp and got[k]]
        bad = [k for k in exp if k in got and not close(got[k], exp[k])]
        if miss or extra or bad:
            return f"{key} theo {dim}: thiếu {len(miss)}, thừa {len(extra)}, lệch {len(bad)} dòng"
        return None
    if chk.get("trend"):
        for b in res.get("blocks") or []:
            data = ((b.get("series") or [{}])[0].get("data")) or []
            if len(data) == len(exp) and all(close(g, e) for g, e in zip(data, exp)):
                return None
        return f"chuỗi tháng {key} ({len(exp)} tháng) không khớp SQL"
    k = next((k for k in res.get("kpis") or [] if k.get("key") == key), None)
    if k is None:
        return f"không có KPI {key}"
    if chk.get("yoy"):
        base = next((c.get("base") for c in k.get("compares") or [] if c.get("label") == "Cùng kỳ"), None)
        ly = expected(chk, user, yoy=True)
        if not close(base, ly, ratio):
            return f"{key} cùng kỳ: chatbot {base} ≠ SQL {ly}"
    if not close(k.get("value"), exp, ratio):
        return f"{key}: chatbot {k.get('value')} ≠ SQL {exp}"
    return None


def _locked(key: str) -> bool:
    if not config.LOCKED_MODULES:
        return False
    from app.chat.engine import DOMAIN_MODULE
    from app.core.semantic import metric
    return DOMAIN_MODULE.get(metric(key)["domain"]) in config.LOCKED_MODULES


def _scope_leak(case: dict, user: User, res: dict) -> str | None:
    if case.get("deny") or user.orgs == frozenset():
        return "có số liệu dù không được phép" if _has_numbers(res) else None
    if user.orgs is None:
        return None
    allowed = {o["org_name"] for o in recon.orgs() if o["org_code"] in user.orgs}
    others = {o["org_name"] for o in recon.orgs()} - allowed
    for rows in _table_blocks(res):
        if any(r.get("org") in others for r in rows):
            return "bảng có đơn vị ngoài phạm vi"
    for b in res.get("blocks") or []:
        if set(b.get("labels") or []) & others:
            return "biểu đồ có đơn vị ngoài phạm vi"
    return None


def load(path: Path = FILE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_case(case: dict, defaults: dict, ask) -> Result:
    """ask(question, ctx, user, history, last) -> kết quả engine.answer."""
    user = USERS[case.get("user", "full")]
    r = Result(case["id"], case.get("split", "dev"), case.get("group", ""), case.get("user", "full"), case["q"])
    ctx = {**defaults.get("ctx", {}), **case.get("ctx", {})}
    history, last = [], None
    if case.get("after"):
        prev = ask(case["after"], ctx, user, [], None)
        history, last = [case["after"]], (prev.get("parts") or [None])[0]
    t0 = time.time()
    res = ask(case["q"], ctx, user, history, last)
    r.ms, r.parser = int((time.time() - t0) * 1000), res.get("parser", "")
    exp = case.get("expect")
    if exp == "help":
        r.intent_ok = res.get("status") == "help"
        if not r.intent_ok:
            r.errors.append(f"mong câu hướng dẫn, nhận status {res.get('status')}")
    elif exp:
        for e in exp if isinstance(exp, list) else [exp]:
            if e.get("metrics") and all(_locked(k) for k in e["metrics"]):
                r.pro_only = True       # bản Lite: chỉ tiêu phân hệ Pro – chỉ kiểm không trả số
                continue
            e = {**e, "metrics": [k for k in e.get("metrics", []) if not _locked(k)]}
            if why := check_intent(e, res.get("parts") or []):
                r.intent_ok = False
                r.errors.append("ý: " + why)
    checks = case.get("check") or []
    if checks:
        r.number_ok = True
        for chk in checks:
            if _locked(chk["metric"]) or any(_locked(k) for k in chk.get("ratio", [])):
                if any(k.get("key") == chk["metric"] and k.get("value") is not None for k in res.get("kpis") or []):
                    r.number_ok = False
                    r.errors.append(f"số: bản Lite vẫn trả {chk['metric']} (phân hệ Pro)")
                continue
            if why := check_number(chk, user, res):
                r.number_ok = False
                r.errors.append("số: " + why)
    if why := _scope_leak(case, user, res):
        r.scope_ok = False
        r.errors.append("phạm vi: " + why)
    return r


def engine_ask(question, ctx, user, history, last):
    from app.chat.engine import answer
    return answer(question, ctx, user, history, last)


def run(split: str = "all", ask=engine_ask, path: Path = FILE, ids: list[str] | None = None) -> list[Result]:
    data = load(path)
    cases = [c for c in data["cases"] if (split == "all" or c.get("split", "dev") == split) and (not ids or c["id"] in ids)]
    return [run_case(c, data, ask) for c in cases]


def score(results: list[Result]) -> dict:
    nums = [r for r in results if r.number_ok is not None and not r.pro_only]
    ms = sorted(r.ms for r in results) or [0]
    return {
        "cases": len(results),
        "intent": sum(r.intent_ok for r in results) / max(len(results), 1),
        "number": sum(bool(r.number_ok) for r in nums) / max(len(nums), 1),
        "number_cases": len(nums),
        "scope_leaks": sum(not r.scope_ok for r in results),
        "all_ok": sum(r.intent_ok and r.number_ok is not False and r.scope_ok for r in results) / max(len(results), 1),
        "median_ms": int(statistics.median(ms)),
        "p95_ms": ms[min(len(ms) - 1, int(0.95 * len(ms)))],
        "pro_only": sum(r.pro_only for r in results),
        "failed": [r.id for r in results if not (r.intent_ok and r.number_ok is not False and r.scope_ok)],
    }


def passed(sc: dict, threshold: float = THRESHOLD) -> bool:
    """Cổng G4: ≥ 95% đúng số và không câu nào lộ ngoài phạm vi."""
    return sc["number"] >= threshold and sc["scope_leaks"] == 0

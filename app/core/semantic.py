"""Lớp ngữ nghĩa + trình biên dịch truy vấn (Blueprint 12.3).

Truy vấn có cấu trúc (chỉ tiêu, chiều, kỳ, bộ lọc) -> SQL tham số hoá trên hợp đồng bi_*.
Chỉ nhận tên đã khai báo trong YAML (whitelist); giá trị lọc luôn là tham số; phạm vi người dùng
được áp ở đây cho MỌI truy vấn (fail-closed). LLM không bao giờ viết SQL.
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app import config
from app.core import db
from app.core.periods import Period
from app.core.scope import User


def _load(name: str) -> dict:
    return yaml.safe_load(open(config.SEMANTIC_DIR / name, encoding="utf-8"))


@lru_cache(maxsize=1)
def model() -> dict:
    return {"sources": _load("sources.yaml"), "dims": _load("dimensions.yaml"), "metrics": _load("metrics.yaml")}


def metric(key: str) -> dict:
    return model()["metrics"][key]


def dim(key: str) -> dict:
    return model()["dims"][key]


class SemanticError(ValueError):
    pass


@dataclass
class Query:
    metrics: list[str]
    dims: list[str] = field(default_factory=list)
    period: Period | None = None
    filters: dict[str, list[str]] = field(default_factory=dict)
    plan: bool = False          # thêm cột <metric>__plan cho chỉ tiêu có plan_sql


@dataclass
class Result:
    rows: list[dict]
    sql: list[str]
    notes: list[str]
    unavailable: set[str]


# ------------------------------------------------------------------ công thức dẫn xuất (an toàn)
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg}


def _deps(expr: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)}


def _eval(expr: str, env: dict) -> float | None:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            v = env.get(n.id)
            if v is None:
                raise ZeroDivisionError
            return float(v)
        if isinstance(n, ast.BinOp):
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp):
            return _OPS[type(n.op)](ev(n.operand))
        raise SemanticError("Công thức không hợp lệ")
    try:
        return ev(ast.parse(expr, mode="eval"))
    except ZeroDivisionError:
        return None


def base_metrics(keys: list[str]) -> list[str]:
    out: list[str] = []

    def walk(k: str):
        m = metric(k)
        if "formula" in m:
            for d in sorted(_deps(m["formula"]) - {"months", "days"}):
                walk(d)
        elif k not in out:
            out.append(k)
    for k in keys:
        if k not in model()["metrics"]:
            raise SemanticError(f"Chỉ tiêu không tồn tại: {k}")
        walk(k)
    return out


def derived_order(keys: list[str]) -> list[str]:
    """Các chỉ tiêu dẫn xuất cần tính, theo thứ tự phụ thuộc."""
    out: list[str] = []

    def walk(k: str):
        m = metric(k)
        if "formula" in m:
            for d in sorted(_deps(m["formula"]) - {"months", "days"}):
                walk(d)
            if k not in out:
                out.append(k)
    for k in keys:
        walk(k)
    return out


# ------------------------------------------------------------------ biên dịch 1 nguồn
def _compile(source: str, metrics: list[str], q: Query, user: User | None) -> tuple[str, dict]:
    src = model()["sources"][source]
    t = src["table"]
    joins: set[str] = set()
    select, group, params, where = [], [], {}, []
    for d in q.dims:
        dd = dim(d)
        col = dd["columns"].get(source)
        if col is None:
            raise SemanticError(f"{src['label']} không tách được theo {dd['label']}")
        if dd.get("join"):
            joins.add(dd["join"])
        select.append(f"{col} AS d_{d}")
        group.append(col)
        if dd.get("order"):
            select.append(f"{dd['order']} AS o_{d}")
    for k in metrics:
        m = metric(k)
        joins.update(m.get("joins", []))
        select.append(f"{m['sql']} AS m_{k}")
        if q.plan and m.get("plan_sql"):
            select.append(f"{m['plan_sql']} AS m_{k}__plan")
    # kỳ
    p = q.period
    if p is not None:
        time_grouped = any(dim(d).get("time") for d in q.dims)
        if src["kind"] == "snapshot" and not time_grouped:
            where.append("t.year*100+t.month = :p_end")
        else:
            where.append("t.year*100+t.month BETWEEN :p_start AND :p_end")
        params.update(p_start=p.start, p_end=p.end)
    # bộ lọc (giá trị luôn là tham số)
    for i, (d, values) in enumerate(q.filters.items()):
        if not values:
            continue
        dd = dim(d)
        col = dd["columns"].get(source)
        if col is None:
            raise SemanticError(f"{src['label']} không lọc được theo {dd['label']}")
        if dd.get("join"):
            joins.add(dd["join"])
        names = []
        for j, v in enumerate(values):
            params[f"f{i}_{j}"] = v
            names.append(f":f{i}_{j}")
        where.append(f"{col} IN ({', '.join(names)})")
    # phạm vi người dùng – fail-closed
    if user is not None and not user.is_all:
        oc = src.get("org_column")
        if not oc:
            raise SemanticError(f"Phạm vi dữ liệu của bạn không áp dụng được cho nguồn {src['label']}")
        if not user.orgs:
            where.append("1 = 0")
        else:
            names = []
            for j, v in enumerate(sorted(user.orgs)):
                params[f"s_{j}"] = v
                names.append(f":s_{j}")
            where.append(f"t.{oc} IN ({', '.join(names)})")
    sql = f"SELECT {', '.join(select)} FROM {t} t"
    if "org" in joins:
        sql += " LEFT JOIN bi_org o ON o.org_code = t.org_code"
    if "line" in joins:
        sql += f" LEFT JOIN {src['line_table']} l ON l.line_code = t.line_code"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group:
        sql += " GROUP BY " + ", ".join(group)
    return sql, params


def run(q: Query, user: User | None) -> Result:
    bases = base_metrics(q.metrics)
    by_source: dict[str, list[str]] = {}
    for k in bases:
        by_source.setdefault(metric(k)["source"], []).append(k)
    merged: dict[tuple, dict] = {}
    sqls, notes, unavailable = [], [], set()
    for source, keys in by_source.items():
        try:
            sql, params = _compile(source, keys, q, user)
        except SemanticError as e:
            notes.append(str(e))
            unavailable.update(keys)
            continue
        sqls.append(sql)
        # [empty-scope-v1] không phạm vi -> không truy vấn, mọi chỉ tiêu trống (COUNT trên tập rỗng = 0 là số sai)
        fetched = ([] if q.dims else [{}]) if user is not None and user.orgs == frozenset() else db.fetch(sql, params)
        for r in fetched:
            key = tuple(r.get(f"d_{d}") for d in q.dims)
            row = merged.setdefault(key, {d: r.get(f"d_{d}") for d in q.dims})
            for d in q.dims:
                if r.get(f"o_{d}") is not None:
                    row[f"_o_{d}"] = r[f"o_{d}"]
            for k in keys:
                row[k] = _f(r.get(f"m_{k}"))
                if f"m_{k}__plan" in r:
                    row[f"{k}__plan"] = _f(r.get(f"m_{k}__plan"))
    env_extra = {"months": q.period.months if q.period else 1, "days": q.period.days if q.period else 30}
    derived = derived_order(q.metrics)
    rows = list(merged.values())
    by_month = "month" in q.dims
    for row in rows:
        for k in bases:
            row.setdefault(k, None)
        if by_month and row.get("month"):  # mỗi dòng là 1 tháng -> số ngày/tháng của chính dòng đó
            ym = int(row["month"])
            env_extra = {"months": 1, "days": Period(ym // 100, ym % 100, ym % 100).days}
        for k in derived:
            row[k] = _eval(metric(k)["formula"], {**row, **env_extra})
            if q.plan:
                plan_env = {b: row.get(f"{b}__plan") for b in bases}
                if all(plan_env.get(b) is not None for b in base_metrics([k])):
                    row[f"{k}__plan"] = _eval(metric(k)["formula"], {**plan_env, **env_extra})
    for k in derived:
        if base_metrics([k]) and set(base_metrics([k])) & unavailable:
            unavailable.add(k)
    if q.dims:
        rows.sort(key=lambda r: tuple(_sort_key(r, d) for d in q.dims))
    return Result(rows, sqls, notes, unavailable)


def _f(v):
    return None if v is None else float(v)


def _sort_key(r: dict, d: str):
    if dim(d).get("time"):
        return r.get(d) or 0
    o = r.get(f"_o_{d}")
    return o if o is not None else 0


def total(metrics: list[str], period: Period, user: User | None, filters: dict | None = None,
          plan: bool = False) -> dict:
    """Tiện ích: một dòng tổng."""
    res = run(Query(metrics, [], period, filters or {}, plan), user)
    row = res.rows[0] if res.rows else {}
    return {k: row.get(k) for k in row} | {"_sql": res.sql, "_notes": res.notes}


@lru_cache(maxsize=1)
def catalog() -> dict:
    """Giá trị các chiều nhỏ để chatbot nhận diện bộ lọc (không gồm tên khách hàng)."""
    out = {}
    for d, sql in {
        "region": "SELECT DISTINCT region_name AS v FROM bi_org",
        "org": "SELECT DISTINCT org_name AS v FROM bi_org",
        "channel": "SELECT DISTINCT channel AS v FROM bi_sales",
        "category": "SELECT DISTINCT category AS v FROM bi_sales",
        "subcategory": "SELECT DISTINCT subcategory AS v FROM bi_sales",
        "customer_type": "SELECT DISTINCT customer_type AS v FROM bi_sales",
    }.items():
        out[d] = sorted(r["v"] for r in db.fetch(sql) if r["v"])
    return out

"""
Query Compiler: Intent (JSON đã kiểm duyệt) -> SQL tham số hóa -> kết quả.

Nguyên tắc Zero-Hallucination:
  1. LLM/Router chỉ được chọn TÊN chỉ số, chiều, giá trị lọc có trong Catalog.
  2. Mọi biểu thức SQL đến từ YAML; giá trị lọc luôn bind qua tham số (:p0, :p1…).
  3. Chỉ số dẫn xuất được tính bằng Python trên kết quả đã tổng hợp cùng hạt dữ liệu.
"""
from __future__ import annotations

import ast
import calendar
import copy
import operator
import re
from dataclasses import dataclass, field
from datetime import date

from .catalog import Catalog

TIME_DIMS = ("nam", "quy", "thang")


class IntentError(Exception):
    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.suggestions = suggestions or []


@dataclass
class Intent:
    metrics: list[str]
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    year: int | None = None
    quarters: list[int] = field(default_factory=list)
    months: list[int] = field(default_factory=list)
    compare: str | None = None          # None | "prev"
    order: str | None = None            # None | "desc" | "asc"
    limit: int | None = None

    def to_dict(self) -> dict:
        return copy.deepcopy(self.__dict__)


@dataclass
class QueryResult:
    intent: Intent
    dims: list[str]
    metrics: list[str]
    rows: list[dict]
    totals: dict | None
    prev_totals: dict | None
    period_label: str
    prev_label: str | None
    sql: list[str]
    notes: list[str]


# --------------------------------------------------------------------------- utils
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_REF = re.compile(r"\{([a-z_0-9]+)\}")


def eval_formula(formula: str, values: dict) -> float | None:
    expr = ast.parse(_REF.sub(lambda m: m.group(1), formula), mode="eval").body

    def ev(node):
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            a, b = ev(node.left), ev(node.right)
            if a is None or b is None:
                return None
            if isinstance(node.op, ast.Div) and b == 0:
                return None
            return _OPS[type(node.op)](a, b)
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Biểu thức không hợp lệ")

    return ev(expr)


def _expand(expr: str, macros: dict[str, str]) -> str:
    for _ in range(3):
        expr = re.sub(r"\{([a-z_]+)\}", lambda m: macros.get(m.group(1), m.group(0)), expr)
    return expr


def _render_sql(sql: str, params: dict) -> str:
    def rep(m):
        v = params[m.group(1)]
        return f"'{v}'" if isinstance(v, str) else str(v)
    return re.sub(r":([a-z_0-9]+)", rep, sql)


def period_text(year: int | None, quarters: list[int], months: list[int]) -> str:
    y = f"/{year}" if year else ""
    if months:
        return ", ".join(f"Tháng {m}" for m in months) + y
    if quarters:
        if len(quarters) > 1 and quarters == list(range(quarters[0], quarters[-1] + 1)):
            return f"Quý {quarters[0]}–{quarters[-1]}{y}"
        return ", ".join(f"Quý {q}" for q in quarters) + y
    return f"Năm {year}" if year else "Toàn bộ dữ liệu"


# ------------------------------------------------------------------------ compiler
class Compiler:
    def __init__(self, catalog: Catalog):
        self.cat = catalog
        self.db = catalog.db
        self.data_end = date.fromisoformat(self.db.meta().get("ngay_chot_du_lieu", "2025-12-31"))

    # ------------------------------------------------------------- validation
    def validate(self, raw: dict) -> Intent:
        cat = self.cat
        metrics = [m for m in (raw.get("metrics") or []) if m]
        unknown = [m for m in metrics if m not in cat.metrics]
        if unknown or not metrics:
            raise IntentError(
                "Em chưa xác định được chỉ số cần tra cứu trong Semantic Layer." if not metrics else
                f"Chỉ số không có trong Semantic Layer: {', '.join(unknown)}.",
                [f"{m.label}" for m in list(cat.metrics.values())[:8]])
        dims = []
        for d in raw.get("dimensions") or []:
            if d not in cat.dimensions:
                raise IntentError(f"Chiều phân tích không tồn tại: {d}.")
            if d not in dims:
                dims.append(d)
        filters: dict[str, list[str]] = {}
        for d, vals in (raw.get("filters") or {}).items():
            if d in TIME_DIMS:
                continue
            if d not in cat.dimensions:
                raise IntentError(f"Bộ lọc theo chiều không tồn tại: {d}.")
            vals = vals if isinstance(vals, list) else [vals]
            canon = []
            for v in vals:
                c = cat.match_value(d, v)
                if c is None:
                    raise IntentError(f"Không tìm thấy giá trị “{v}” trong {cat.dimensions[d].label}.",
                                      cat.dimensions[d].values[:8])
                if c not in canon:
                    canon.append(c)
            if canon:
                filters[d] = canon
        flt = raw.get("filters") or {}
        year = raw.get("year") or flt.get("nam")
        year = int(year[0] if isinstance(year, list) else year) if year else self.data_end.year
        quarters = sorted({int(q) for q in (raw.get("quarters") or flt.get("quy") or []) if 1 <= int(q) <= 4})
        months = sorted({int(m) for m in (raw.get("months") or flt.get("thang") or []) if 1 <= int(m) <= 12})
        for m in metrics:
            sup = cat.supported_dims(m)
            bad = [d for d in dims + list(filters) if d not in sup]
            if bad:
                raise IntentError(
                    f"Chỉ số “{cat.metrics[m].label}” không phân tích được theo "
                    f"{', '.join(cat.dimensions[d].label for d in bad)}.",
                    [f"Chiều hỗ trợ: {', '.join(cat.dimensions[d].label for d in sorted(sup))}"])
            if months and "thang" not in sup:
                raise IntentError(f"Chỉ số “{cat.metrics[m].label}” chỉ có dữ liệu theo Quý, không theo Tháng.")
        compare = raw.get("compare") if raw.get("compare") in ("prev",) else None
        order = raw.get("order") if raw.get("order") in ("asc", "desc") else None
        limit = raw.get("limit")
        limit = max(1, min(int(limit), 100)) if limit else None
        if limit and not order:
            order = "desc"
        return Intent(metrics=metrics, dimensions=dims, filters=filters, year=year, quarters=quarters,
                      months=months, compare=compare, order=order, limit=limit)

    # -------------------------------------------------------------- execution
    def _asof_for(self, year: int, quarters: list[int], months: list[int]) -> date:
        if months:
            d = date(year, max(months), calendar.monthrange(year, max(months))[1])
        elif quarters:
            m = max(quarters) * 3
            d = date(year, m, calendar.monthrange(year, m)[1])
        else:
            d = date(year, 12, 31)
        return min(d, self.data_end)

    def _run_source(self, src_key: str, base: list[str], dims: list[str], it: Intent,
                    sql_log: list[str]) -> dict[tuple, dict]:
        src = self.cat.sources[src_key]
        params: dict = {}
        out: dict[tuple, dict] = {}

        def bind(v):
            k = f"p{len(params)}"
            params[k] = v
            return f":{k}"

        def one(asof: date | None, extra_dims: dict):
            params.clear()
            where = []
            if src.kind == "snapshot":
                params["asof"] = asof.isoformat()
                where.append(_expand(src.where, src.macros))
            else:
                if it.year:
                    where.append(f"nam = {bind(it.year)}")
                if it.quarters:
                    where.append(f"quy IN ({', '.join(bind(q) for q in it.quarters)})")
                if it.months:
                    where.append(f"thang IN ({', '.join(bind(m) for m in it.months)})")
            for d, vals in it.filters.items():
                col = _expand(src.dimensions[d], src.macros)
                where.append(f"{col} IN ({', '.join(bind(v) for v in vals)})")
            sel_dims = [d for d in dims if d in src.dimensions]
            sel = [f"{_expand(src.dimensions[d], src.macros)} AS {d}" for d in sel_dims]
            sel += [f"{_expand(self.cat.metrics[m].sql, src.macros)} AS {m}" for m in base]
            sql = f"SELECT {', '.join(sel)}\nFROM {src.table}"
            if where:
                sql += "\nWHERE " + "\n  AND ".join(where)
            if sel_dims:
                sql += "\nGROUP BY " + ", ".join(_expand(src.dimensions[d], src.macros) for d in sel_dims)
            cols, rows = self.db.query(sql, dict(params))
            sql_log.append(_render_sql(sql, params))
            for r in rows:
                rec = dict(zip(cols, r))
                rec.update(extra_dims)
                key = tuple(rec.get(d) for d in dims)
                out.setdefault(key, {}).update({m: rec[m] for m in base})

        if src.kind == "snapshot":
            tdims = [d for d in dims if d in TIME_DIMS]
            if tdims:
                for label, asof in self._snapshot_periods(it, tdims[0]):
                    one(asof, {tdims[0]: label})
            else:
                one(self._asof_for(it.year, it.quarters, it.months), {})
        else:
            one(None, {})
        return out

    def _snapshot_periods(self, it: Intent, tdim: str) -> list[tuple[int, date]]:
        y = it.year
        if tdim == "thang":
            months = it.months or ([m for q in it.quarters for m in range(q * 3 - 2, q * 3 + 1)] or range(1, 13))
            res = [(m, date(y, m, calendar.monthrange(y, m)[1])) for m in months]
        elif tdim == "quy":
            qs = it.quarters or [1, 2, 3, 4]
            res = [(q, date(y, q * 3, calendar.monthrange(y, q * 3)[1])) for q in qs]
        else:
            res = [(y, date(y, 12, 31))]
        return [(lbl, d) for lbl, d in res if d <= self.data_end]

    def _execute(self, it: Intent, dims: list[str], sql_log: list[str]) -> list[dict]:
        need: dict[str, list[str]] = {}
        for m in it.metrics:
            for b in self.cat.base_metrics(m):
                s = self.cat.metrics[b].source
                need.setdefault(s, [])
                if b not in need[s]:
                    need[s].append(b)
        merged: dict[tuple, dict] = {}
        for s, base in need.items():
            for key, vals in self._run_source(s, base, dims, it, sql_log).items():
                merged.setdefault(key, {}).update(vals)
        rows = []
        for key, vals in merged.items():
            row = {d: key[i] for i, d in enumerate(dims)}
            row.update(vals)
            for m in it.metrics:
                if self.cat.metrics[m].is_derived:
                    row[m] = self._derive(m, row)
            rows.append(row)
        return rows

    def _derive(self, key: str, row: dict):
        m = self.cat.metrics[key]
        vals = {}
        for r in m.refs():
            vals[r] = self._derive(r, row) if self.cat.metrics[r].is_derived else row.get(r)
        return eval_formula(m.formula, vals)

    def prev_period(self, it: Intent) -> tuple[int, list[int], list[int]] | None:
        if it.months and len(it.months) == 1:
            m = it.months[0]
            return (it.year, [], [m - 1]) if m > 1 else (it.year - 1, [], [12])
        if it.quarters and len(it.quarters) == 1 and not it.months:
            q = it.quarters[0]
            return (it.year, [q - 1], []) if q > 1 else (it.year - 1, [4], [])
        return None

    def run(self, it: Intent) -> QueryResult:
        sql_log: list[str] = []
        notes: list[str] = []
        dims = it.dimensions
        if it.compare == "prev" and not it.quarters and not it.months:
            it.quarters = [((self.data_end.month - 1) // 3) + 1]
            notes.append("Chưa chỉ định kỳ, em so sánh quý gần nhất với quý liền trước.")
        rows = self._execute(it, dims, sql_log)
        totals = None
        if dims:
            t = self._execute(it, [], sql_log)
            totals = t[0] if t else None
        else:
            totals = rows[0] if rows else None

        prev_totals, prev_label = None, None
        has_time_dim = any(d in TIME_DIMS for d in dims)
        if it.compare == "prev":
            pp = self.prev_period(it)
            if pp is None or has_time_dim:
                notes.append("So sánh kỳ trước chỉ áp dụng khi chọn đúng 1 quý hoặc 1 tháng và không phân rã theo thời gian.")
                it.compare = None
            else:
                pit = Intent(**{**it.to_dict(), "year": pp[0], "quarters": pp[1], "months": pp[2], "compare": None})
                prev_label = period_text(pp[0], pp[1], pp[2])
                prev_rows = {tuple(r.get(d) for d in dims): r for r in self._execute(pit, dims, sql_log)}
                cur_keys = {tuple(r.get(d) for d in dims) for r in rows}
                for key in prev_rows:  # có ở kỳ trước nhưng không phát sinh kỳ này
                    if key not in cur_keys:
                        blank = {d: key[i] for i, d in enumerate(dims)}
                        for m in it.metrics:
                            blank[m] = 0 if self.cat.metrics[m].additive else None
                        rows.append(blank)
                for r in rows:
                    p = prev_rows.get(tuple(r.get(d) for d in dims), {})
                    for m in it.metrics:
                        r[f"{m}__prev"] = p.get(m)
                if dims:
                    t = self._execute(pit, [], sql_log)
                    prev_totals = t[0] if t else None
                else:
                    prev_totals = {m: rows[0].get(f"{m}__prev") for m in it.metrics} if rows else None

        # ---- sắp xếp & giới hạn
        first = it.metrics[0]
        ordered_dim = next((d for d in dims if self.cat.dimensions[d].ordered), None)
        if ordered_dim and not it.order:
            pos = {v: i for i, v in enumerate(self.cat.dimensions[ordered_dim].values)}
            rows.sort(key=lambda r: tuple(pos.get(r.get(d), 99) if d == ordered_dim else 0 for d in dims))
        elif it.order or (dims and not has_time_dim):
            rev = (it.order or "desc") == "desc"
            rows.sort(key=lambda r: (r.get(first) is None, -(r.get(first) or 0) if rev else (r.get(first) or 0)))
        elif has_time_dim:
            rows.sort(key=lambda r: tuple(r.get(d) or 0 for d in dims))
        if it.limit:
            rows = rows[: it.limit]

        label = period_text(it.year, it.quarters, it.months)
        is_snapshot = all(self.cat.is_snapshot(m) for m in it.metrics)
        if is_snapshot and not has_time_dim:
            asof = self._asof_for(it.year, it.quarters, it.months)
            label = f"Ngày chốt {asof.strftime('%d/%m/%Y')}"
            if prev_label and it.compare == "prev":
                pp = self.prev_period(it)
                prev_label = f"Ngày chốt {self._asof_for(*pp).strftime('%d/%m/%Y')}"
        if any(self.cat.is_snapshot(m) for m in it.metrics) and not has_time_dim:
            asof = self._asof_for(it.year, it.quarters, it.months)
            notes.append(f"Số dư công nợ tính tại ngày chốt {asof.strftime('%d/%m/%Y')}.")
        return QueryResult(intent=it, dims=dims, metrics=it.metrics, rows=rows, totals=totals,
                           prev_totals=prev_totals, period_label=label, prev_label=prev_label,
                           sql=sql_log, notes=notes)

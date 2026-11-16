"""
Formatter tất định (deterministic): biến QueryResult thành các "khối" hiển thị
(text / table / chart). Không dùng LLM để viết số => con số luôn khớp database.
Phần Nhận xét / Đánh giá / Kết luận / Đề xuất / Gợi ý do insights.py đảm nhận.
"""
from __future__ import annotations

from .catalog import Catalog
from .compiler import TIME_DIMS, QueryResult


# ------------------------------------------------------------------ định dạng số
def _vn(x: float, dec: int) -> str:
    s = f"{x:,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def money_unit(values: list) -> tuple[float, str]:
    m = max((abs(v) for v in values if isinstance(v, (int, float))), default=0)
    if m >= 1e9:
        return 1e9, "tỷ"
    if m >= 1e6:
        return 1e6, "triệu"
    return 1, "đồng"


def fmt(v, kind: str, unit: tuple[float, str] | None = None, with_unit: bool = True) -> str:
    if v is None:
        return "—"
    if kind == "pct":
        return _vn(v * 100, 1) + "%"
    if kind == "qty":
        return _vn(v, 0)
    if kind == "money":
        if v == 0 and unit is None:
            return "0"
        div, name = unit or money_unit([v])
        s = _vn(v / div, 2 if div >= 1e6 else 0)
        return f"{s} {name}" if with_unit else s
    return _vn(v, 2)


def change_text(cur, prev, kind: str) -> str:
    if cur is None or prev is None:
        return "—"
    if kind == "pct":
        d = (cur - prev) * 100
        return ("+" if d >= 0 else "") + _vn(d, 1) + " điểm %"
    if prev == 0:
        return "—"
    g = cur / prev - 1
    return ("+" if g >= 0 else "") + _vn(g * 100, 1) + "%"


def tone(cur, prev, higher_is_better: bool = True) -> str:
    """'pos' = tốt, 'neg' = xấu (theo chiều tốt của chỉ số), '' nếu không xác định."""
    if cur is None or prev is None or cur == prev:
        return ""
    up = cur > prev
    return "pos" if up == higher_is_better else "neg"


def dim_value(dim: str, v) -> str:
    if dim == "quy":
        return f"Q{v}"
    if dim == "thang":
        return f"T{v}"
    return str(v)


# --------------------------------------------------------------------- formatter
class Formatter:
    def __init__(self, catalog: Catalog):
        self.cat = catalog

    def render(self, r: QueryResult) -> list[dict]:
        cat = self.cat
        it = r.intent
        mlabels = [cat.metrics[m].label for m in r.metrics]
        title = f"**{' · '.join(mlabels)}** — {r.period_label}"
        if r.dims:
            title += " · theo " + ", ".join(cat.dimensions[d].label for d in r.dims)
        if it.filters:
            title += " · " + "; ".join(f"{cat.dimensions[d].label}: {', '.join(v)}" for d, v in it.filters.items())
        blocks: list[dict] = []
        lines = [title, ""]

        if not r.rows:
            lines.append("Không có dữ liệu phù hợp với điều kiện lọc.")
            return [{"type": "text", "md": "\n".join(lines)}]

        has_time = any(d in TIME_DIMS for d in r.dims)
        if not r.dims:
            row = r.rows[0]
            for m in r.metrics:
                k = cat.metrics[m].fmt
                line = f"- {cat.metrics[m].label}: **{fmt(row.get(m), k)}**"
                if it.compare == "prev" and r.prev_label:
                    p = row.get(f"{m}__prev")
                    line += f" ({change_text(row.get(m), p, k)} so với {r.prev_label}: {fmt(p, k)})"
                lines.append(line)
        elif r.totals and not has_time:
            for m in r.metrics:
                mm = cat.metrics[m]
                if r.totals.get(m) is None:
                    continue
                head = f"Tổng {mm.label[0].lower() + mm.label[1:]}" if mm.additive else f"{mm.label} toàn bộ"
                tot_line = f"- {head}: **{fmt(r.totals[m], mm.fmt)}**"
                if it.compare == "prev" and r.prev_totals:
                    tot_line += f" ({change_text(r.totals[m], r.prev_totals.get(m), mm.fmt)} so với {r.prev_label})"
                lines.append(tot_line)
        for n in r.notes:
            lines.append(f"\n_{n}_")
        blocks.append({"type": "text", "md": "\n".join(lines)})

        # ---- bảng
        cols = [{"key": d, "label": cat.dimensions[d].label, "align": "left"} for d in r.dims]
        units = {}
        for m in r.metrics:
            k = cat.metrics[m].fmt
            unit_lbl = ""
            if k == "money":
                units[m] = money_unit([x.get(m) for x in r.rows] + [x.get(f"{m}__prev") for x in r.rows])
                unit_lbl = f" ({units[m][1]})"
            elif k == "pct":
                unit_lbl = " (%)"
            if it.compare == "prev" and r.prev_label:
                cols.append({"key": f"{m}__prev", "label": f"{cat.metrics[m].label} {r.prev_label}{unit_lbl}", "align": "right"})
                cols.append({"key": m, "label": f"{cat.metrics[m].label} {r.period_label}{unit_lbl}", "align": "right"})
                cols.append({"key": f"{m}__chg", "label": "Biến động", "align": "right"})
            else:
                cols.append({"key": m, "label": cat.metrics[m].label + unit_lbl, "align": "right"})

        def cell_for(m):
            k = cat.metrics[m].fmt
            return lambda v: fmt(v, k, units.get(m), with_unit=False) if k != "pct" else fmt(v, k)

        trows, tones = [], []
        for x in r.rows:
            out = [dim_value(d, x.get(d)) for d in r.dims]
            tn = [""] * len(r.dims)
            for m in r.metrics:
                k = cat.metrics[m].fmt
                cell = cell_for(m)
                if it.compare == "prev" and r.prev_label:
                    out += [cell(x.get(f"{m}__prev")), cell(x.get(m)), change_text(x.get(m), x.get(f"{m}__prev"), k)]
                    tn += ["", "", tone(x.get(m), x.get(f"{m}__prev"), cat.metrics[m].higher_is_better)]
                else:
                    out.append(cell(x.get(m)))
                    tn.append("")
            trows.append(out)
            tones.append(tn)
        footer = False
        if r.dims and r.totals and not has_time and len(r.rows) > 1:
            tot = ["Tổng toàn bộ" if it.limit else "Tổng cộng"] + [""] * (len(r.dims) - 1)
            ttn = [""] * len(r.dims)
            for m in r.metrics:
                k = cat.metrics[m].fmt
                cell = cell_for(m)
                if it.compare == "prev" and r.prev_label:
                    pv = (r.prev_totals or {}).get(m)
                    tot += [cell(pv), cell(r.totals.get(m)), change_text(r.totals.get(m), pv, k)]
                    ttn += ["", "", tone(r.totals.get(m), pv, cat.metrics[m].higher_is_better)]
                else:
                    tot.append(cell(r.totals.get(m)))
                    ttn.append("")
            trows.append(tot)
            tones.append(ttn)
            footer = True
        blocks.append({"type": "table", "columns": cols, "rows": trows, "tones": tones, "footer": footer})

        # ---- biểu đồ
        if len(r.dims) == 1 and 2 <= len(r.rows) <= 24:
            m0 = r.metrics[0]
            k = cat.metrics[m0].fmt
            unit = units.get(m0) if k == "money" else None
            conv = (lambda v: None if v is None else round(v / unit[0], 3)) if unit else \
                   (lambda v: None if v is None else round(v * 100, 2)) if k == "pct" else (lambda v: v)
            series = []
            if it.compare == "prev" and r.prev_label:
                series.append({"name": r.prev_label, "values": [conv(x.get(f"{m0}__prev")) for x in r.rows], "role": "prior"})
            series.append({"name": r.period_label if (it.compare == "prev" and r.prev_label) else cat.metrics[m0].label,
                           "values": [conv(x.get(m0)) for x in r.rows], "role": "main"})
            if len(r.metrics) > 1 and cat.metrics[r.metrics[1]].fmt == "pct" and k != "pct":
                series.append({"name": cat.metrics[r.metrics[1]].label, "role": "line",
                               "values": [None if x.get(r.metrics[1]) is None else round(x[r.metrics[1]] * 100, 2) for x in r.rows]})
            blocks.append({
                "type": "chart",
                "kind": "line" if r.dims[0] in TIME_DIMS and len(r.rows) > 4 else "bar",
                "title": f"{cat.metrics[m0].label} ({unit[1] if unit else ('%' if k == 'pct' else '')})".replace(" ()", ""),
                "labels": [dim_value(r.dims[0], x.get(r.dims[0])) for x in r.rows],
                "series": series,
            })
        return blocks

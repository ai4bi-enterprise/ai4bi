"""Khối dựng dùng chung cho mọi phân hệ: thẻ KPI, biểu đồ, bảng, nhận định.

Khuôn đầu ra này là HỢP ĐỒNG với giao diện: dashboard và chatbot cùng render một định dạng (Blueprint P2).
"""
from __future__ import annotations

from app.core import fmt
from app.core.periods import Period, ym_label
from app.core.scope import User
from app.core.semantic import Query, metric, run

PLAN_GOOD, PLAN_WARN = 1.0, 0.95   # Đạt ≥100%, Gần đạt 95–<100%, Chưa đạt <95% (Blueprint 11.1)


def plan_status(ratio: float | None) -> tuple[str, str]:
    if ratio is None:
        return "info", "Chưa có kế hoạch"
    if ratio >= PLAN_GOOD:
        return "good", "Đạt"
    if ratio >= PLAN_WARN:
        return "warn", "Gần đạt"
    return "bad", "Chưa đạt"


def assess(key: str, v: float | None) -> str | None:
    a = metric(key).get("assess")
    if not a or v is None:
        return None
    up = metric(key).get("better", "up") == "up"
    if (v >= a["good"]) if up else (v <= a["good"]):
        return "good"
    if (v >= a["warn"]) if up else (v <= a["warn"]):
        return "warn"
    return "bad"


def compare(key: str, cur: float | None, base: float | None, label: str) -> dict:
    m = metric(key)
    kind = m.get("format", "money")
    better = m.get("better")
    if cur is None or base is None:
        return {"label": label, "base": base, "base_display": fmt.value(base, kind), "delta_display": "–", "tone": "info"}
    if kind == "pct":
        delta, disp = cur - base, fmt.pp(cur - base)
    else:
        delta = fmt.growth(cur, base)
        disp = fmt.signed_pct(delta) if delta is not None else "–"
    tone = "info"
    if delta is not None and better and abs(delta) > 1e-9:
        tone = "good" if (delta > 0) == (better == "up") else "bad"
    return {"label": label, "base": base, "base_display": fmt.value(base, kind), "delta": delta,
            "delta_display": disp, "tone": tone}


def kpi(key: str, cur: float | None, compares: list[dict] | None = None, label: str | None = None,
        sub: str | None = None, ask: str | None = None, tone: str | None = None) -> dict:
    m = metric(key)
    return {"key": key, "label": label or m["label"], "value": cur, "format": m.get("format", "money"),
            "display": fmt.value(cur, m.get("format", "money")), "compares": compares or [],
            "sub": sub, "tone": tone or assess(key, cur), "ask": ask or f"Phân tích {m['label'].lower()}"}


def chart(id_: str, title: str, kind: str, labels: list, series: list[dict], fmt_: str = "money",
          ask: str | None = None, width: str = "half", note: str | None = None) -> dict:
    return {"id": id_, "type": "chart", "title": title, "kind": kind, "labels": labels, "series": series,
            "format": fmt_, "ask": ask, "width": width, "note": note}


def table(id_: str, title: str, columns: list[dict], rows: list[dict], ask: str | None = None,
          width: str = "half", note: str | None = None) -> dict:
    return {"id": id_, "type": "table", "title": title, "columns": columns, "rows": rows, "ask": ask,
            "width": width, "note": note}


def col(key: str, label: str, format_: str = "text", tone: bool = False) -> dict:
    return {"key": key, "label": label, "format": format_, "tone": tone}


def series(name: str, data: list, type_: str | None = None, axis: str | None = None,
           color: str | None = None, fmt_: str | None = None) -> dict:
    return {"name": name, "data": data, "type": type_, "axis": axis, "color": color, "format": fmt_}


def monthly(metrics: list[str], period: Period, user: User, filters: dict) -> dict[int, dict]:
    res = run(Query(metrics, ["month"], period, filters), user)
    return {r["month"]: r for r in res.rows}


def months_of(period: Period) -> list[int]:
    out, y, m = [], period.y_from or period.year, period.m_from
    for _ in range(period.months):
        out.append(y * 100 + m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_labels(yms: list[int]) -> list[str]:
    return [ym_label(x) for x in yms]


def waterfall(id_: str, title: str, steps: list[tuple[str, float | None, str]], ask: str | None = None,
              width: str = "half", note: str | None = None) -> dict:
    """steps: (nhãn, giá trị, kiểu) – kiểu 'total' là cột mốc, 'delta' là phần tăng/giảm."""
    return {"id": id_, "type": "chart", "title": title, "kind": "waterfall", "labels": [s[0] for s in steps],
            "series": [series("Giá trị", [s[1] for s in steps])], "steps": [s[2] for s in steps],
            "format": "money", "ask": ask, "width": width, "note": note}


def safe_div(a, b):
    return None if a is None or not b else a / b


def page(module: str, title: str, period: Period, kpis: list[dict], blocks: list[dict], insights: list[dict],
         notes: list[str] | None = None, sql: list[str] | None = None) -> dict:
    return {"module": module, "title": title, "period": period.to_dict(), "kpis": kpis, "blocks": blocks,
            "insights": insights, "notes": notes or [], "sql": sql or []}

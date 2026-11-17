"""Xuất câu trả lời chatbot: Excel (export-xlsx) và trang HTML để chia sẻ (share-log) – Blueprint 10.3, 13.3, Phụ lục A.

Đầu vào là chính các khối câu trả lời trình duyệt đang hiển thị (KPI, bảng, biểu đồ -> bảng), không chạy lại truy vấn.
- Excel: giữ số gốc + định dạng số, tiêu đề bảng cố định; chuỗi bắt đầu bằng = + - @ được thêm ' (chống chèn công thức).
- HTML: một file tự chứa, mọi nội dung đã escape, CSP cấm script; mở được khi không có mạng / không đăng nhập.
"""
from __future__ import annotations

import html
import io
import re
from datetime import datetime, timedelta, timezone

from app.core import fmt

VN = timezone(timedelta(hours=7))
MAX_ROWS = 5000
NUMERIC = ("money", "pct", "signed_pct", "number", "days", "times")
XL_FMT = {"money": "#,##0;[Red]-#,##0", "pct": "0.0%", "signed_pct": "+0.0%;[Red]-0.0%", "number": "#,##0",
          "days": "#,##0", "times": "0.00"}


# ------------------------------------------------------------------ khối -> bảng (giống chartAsTable ở render.js)
def block_table(b: dict) -> tuple[list[dict], list[dict]]:
    if b.get("type") == "table":
        return b.get("columns") or [], b.get("rows") or []
    if isinstance(b.get("table"), dict):
        return b["table"].get("columns") or [], b["table"].get("rows") or []
    labels, series, f = b.get("labels") or [], b.get("series") or [], b.get("format") or "money"
    if b.get("kind") == "waterfall":
        data = (series[0].get("data") if series else None) or []
        return ([{"key": "l", "label": "Khoản mục"}, {"key": "v", "label": "Giá trị", "format": f}],
                [{"l": lab, "v": data[i] if i < len(data) else None} for i, lab in enumerate(labels)])
    cols = [{"key": "l", "label": ""}] + [{"key": f"s{i}", "label": s.get("name", ""), "format": f}
                                          for i, s in enumerate(series)]
    rows = []
    for j, lab in enumerate(labels):
        row = {"l": lab}
        for i, s in enumerate(series):
            d = s.get("data") or []
            row[f"s{i}"] = d[j] if j < len(d) else None
        rows.append(row)
    return cols, rows


def _num(v) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _plain(md: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*|\*(\S.*?)\*|`(.+?)`", lambda m: next(g for g in m.groups() if g), md or "")


def tables(ans: dict) -> list[tuple[str, list[dict], list[dict]]]:
    out = []
    for b in ans.get("blocks") or []:
        if isinstance(b, dict):
            cols, rows = block_table(b)
            if cols:
                out.append((str(b.get("title") or "Bảng"), cols, rows[:MAX_ROWS]))
    return out


# ------------------------------------------------------------------ Excel
def xlsx(question: str, ans: dict, company: str, who: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2563EB")
    wb = Workbook()
    ws = wb.active
    ws.title = "Tóm tắt"
    ws.append([_text(f"AI4BI – {company}")])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([_text(f"Xuất bởi {who} lúc {datetime.now(VN):%d/%m/%Y %H:%M}. Số liệu trong phạm vi dữ liệu của người xuất.")])
    ws.append([])
    ws.append(["Câu hỏi", _text(question)])
    ws.append(["Trả lời", _text(_plain(ans.get("answer") or ""))])
    ws["B5"].alignment = Alignment(wrap_text=True, vertical="top")
    kpis = [k for k in ans.get("kpis") or [] if isinstance(k, dict)]
    if kpis:
        ws.append([])
        ws.append(["Chỉ số", "Giá trị", "Hiển thị", "So sánh"])
        for c in ws[ws.max_row]:
            c.font, c.fill = head, fill
        for k in kpis:
            ws.append([_text(k.get("label")), _num(k.get("value")), _text(k.get("display")),
                       _text("; ".join(f"{x.get('label')}: {x.get('delta_display')}" for x in k.get("compares") or []
                                       if isinstance(x, dict)))])
            ws.cell(ws.max_row, 2).number_format = XL_FMT.get(k.get("format"), "#,##0")
    for n in ans.get("notes") or []:
        ws.append([_text(f"ⓘ {n}")])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 90
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 50

    used = {"Tóm tắt"}
    for title, cols, rows in tables(ans):
        name = re.sub(r"[\[\]:*?/\\]", " ", title).strip()[:28] or "Bảng"
        base, i = name, 2
        while name.lower() in {u.lower() for u in used}:
            name, i = f"{base[:25]} ({i})", i + 1
        used.add(name)
        sh = wb.create_sheet(name)
        sh.append([_text(c.get("label") or "") for c in cols])
        for c in sh[1]:
            c.font, c.fill = head, fill
        sh.freeze_panes = "A2"                                   # tiêu đề cố định
        for r in rows:
            if isinstance(r, dict):
                sh.append([(_num(r.get(c.get("key"))) if c.get("format") in NUMERIC else _text(r.get(c.get("key"))))
                           for c in cols])
        for j, c in enumerate(cols, start=1):
            letter = get_column_letter(j)
            sh.column_dimensions[letter].width = max(12, min(45, len(str(c.get("label") or "")) + 4))
            if c.get("format") in NUMERIC:
                for cell in sh[letter][1:]:
                    cell.number_format = XL_FMT[c["format"]]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ HTML chia sẻ
def _md(text: str) -> str:
    """Giống md() ở render.js: escape trước rồi mới định dạng."""
    out, in_list = [], False
    for line in html.escape(text or "").split("\n"):
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"(^|[^*])\*(?!\s)(.+?)\*", r"\1<i>\2</i>", line)
        line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
        if re.match(r"^\s*[-•]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{re.sub(r'^\s*[-•]\s+', '', line)}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p>{line}</p>")
    return "".join(out) + ("</ul>" if in_list else "")


def _cell(v, f: str | None, tone: str | None) -> str:
    e = html.escape
    if f == "badge":
        return f'<td><span class="badge t-{e(tone or "info")}">{e(str(v or ""))}</span></td>'
    if f not in NUMERIC:
        return f"<td>{e('–' if v is None else str(v))}</td>"
    n = _num(v)
    shown = fmt.signed_pct(n) if f == "signed_pct" else fmt.value(n, f)
    cls = "num" + (f" t-{e(tone)}" if tone else " neg" if n is not None and n < 0 and f == "money" else "")
    return f'<td class="{cls}">{e(shown)}</td>'


def _answer_html(ans: dict) -> str:
    e = html.escape
    parts = [f'<div class="md">{_md(ans.get("answer") or "")}</div>'] if ans.get("answer") else []
    kpis = [k for k in ans.get("kpis") or [] if isinstance(k, dict)]
    if kpis:
        parts.append('<div class="kpis">' + "".join(
            f'<div class="kpi"><div class="kl">{e(str(k.get("label") or ""))}</div><div class="kv">{e(str(k.get("display") or ""))}</div>'
            + "".join(f'<div class="kc">{e(str(x.get("label") or ""))}: <b class="t-{e(str(x.get("tone") or ""))}">'
                      f'{e(str(x.get("delta_display") or ""))}</b></div>' for x in k.get("compares") or [] if isinstance(x, dict))
            + "</div>" for k in kpis) + "</div>")
    for title, cols, rows in tables(ans):
        head = "".join(f'<th class="{"num" if c.get("format") in NUMERIC else ""}">{e(str(c.get("label") or ""))}</th>' for c in cols)
        body = "".join("<tr>" + "".join(_cell(r.get(c.get("key")), c.get("format"),
                                              (r.get("_tone") or {}).get(c.get("key")) if isinstance(r.get("_tone"), dict) else None)
                                        for c in cols) + "</tr>" for r in rows if isinstance(r, dict))
        parts.append(f'<section class="card"><h3>{e(title)}</h3><div class="tw"><table><thead><tr>{head}</tr></thead>'
                     f"<tbody>{body}</tbody></table></div></section>")
    notes = [str(n) for n in ans.get("notes") or []]
    if notes:
        parts.append('<div class="notes">' + "".join(f"<div>ⓘ {e(n)}</div>" for n in notes) + "</div>")
    return "".join(parts)


CSS = """
:root{--bg:#f6f7fb;--sf:#fff;--sf2:#f1f4f9;--tx:#0f172a;--mu:#64748b;--bd:#e2e8f0;--ac:#2563eb;--good:#15803d;--bad:#dc2626;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--sf:#111a2e;--sf2:#18233b;--tx:#e5e9f2;--mu:#94a3b8;--bd:#24324d;--ac:#60a5fa;--good:#4ade80;--bad:#f87171;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.55 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px 48px}header{border-bottom:1px solid var(--bd);padding-bottom:12px;margin-bottom:18px}
h1{font-size:20px;margin:0 0 4px}h3{font-size:14px;margin:0 0 8px}.mu{color:var(--mu);font-size:12px}
.turn{margin:0 0 26px}.q{background:var(--ac);color:#fff;padding:8px 12px;border-radius:14px 14px 4px 14px;margin:0 0 10px auto;width:fit-content;max-width:85%}
.md{background:var(--sf2);border-radius:4px 14px 14px 14px;padding:10px 14px}.md p{margin:.3em 0}.md ul{margin:.3em 0;padding-left:20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;margin:10px 0}
.kpi{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:10px 12px}.kl{color:var(--mu);font-size:12px}.kv{font-size:20px;font-weight:600}.kc{font-size:12px;color:var(--mu)}
.card{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:12px;margin:10px 0}.tw{overflow:auto;max-height:520px}
table{border-collapse:collapse;width:100%;font-size:13px}th{position:sticky;top:0;background:var(--sf);text-align:left;border-bottom:2px solid var(--bd);padding:6px 8px;white-space:nowrap}
td{border-bottom:1px solid var(--bd);padding:5px 8px}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.neg,.t-bad{color:var(--bad)}.t-good{color:var(--good)}.t-warn{color:var(--warn)}.badge{padding:1px 8px;border-radius:999px;background:var(--sf2);font-size:12px}
.notes{color:var(--mu);font-size:12px;margin-top:6px}.stale{color:var(--mu);font-style:italic}footer{color:var(--mu);font-size:12px;border-top:1px solid var(--bd);padding-top:10px;margin-top:24px}
"""


def share_html(title: str, turns: list[dict], company: str, who: str) -> str:
    """turns: [{q, r}] – r None = câu trả lời không còn hiển thị được (quyền đã đổi)."""
    e = html.escape
    at = datetime.now(VN)
    body = "".join(
        f'<div class="turn"><div class="q">{e(t.get("q") or "")}</div>'
        + (_answer_html(t["r"]) if isinstance(t.get("r"), dict)
           else '<div class="stale">Câu trả lời không được đưa vào bản chia sẻ.</div>') + "</div>"
        for t in turns)
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<meta name="robots" content="noindex"><title>{e(title)} – AI4BI</title><style>{CSS}</style></head>
<body><main><header><h1>{e(title)}</h1>
<div class="mu">AI4BI – {e(company)} · Chia sẻ bởi {e(who)} lúc {at:%d/%m/%Y %H:%M} (giờ Việt Nam)</div></header>
{body}
<footer>Số liệu nằm trong phạm vi dữ liệu của người chia sẻ tại thời điểm xuất, do code tính từ kho dữ liệu; file này không tự cập nhật.
Chỉ gửi cho người được phép xem các số liệu này.</footer></main></body></html>"""


def filename(stem: str, ext: str) -> str:
    """Tên file ASCII an toàn cho Content-Disposition."""
    import unicodedata
    s = unicodedata.normalize("NFKD", stem.replace("đ", "d").replace("Đ", "D")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60] or "AI4BI"
    return f"AI4BI_{s}_{datetime.now(VN):%Y%m%d_%H%M}.{ext}"

"""Bộ máy trả lời chatbot – luồng rút gọn theo Blueprint 12.2:

① Guard (quyền, xoá PII) → ② Ngữ cảnh → ③ Hiểu câu (LLM hoặc luật) → ④ Kiểm phiếu (whitelist)
→ ⑤⑥ Lập kế hoạch & tính bằng lớp ngữ nghĩa / hàm dựng dashboard (scope áp tại đây)
→ ⑦ Evidence → ⑨ Lời văn (template; LLM chuyên gia nếu bật) → ⑩ Gate → ⑪ Trả về cùng khuôn với dashboard.
"""
from __future__ import annotations

import logging
import re
import time

from app import config
from app.chat import gate, nlu
from app.core import db, fmt
from app.core.periods import Period, make
from app.core.scope import User
from app.core.semantic import Query, SemanticError, catalog, dim, metric, model, run, total
from app.modules import REGISTRY
from app.modules import sales as sales_mod
from app.modules.common import (chart, col, compare, kpi, month_labels, months_of, plan_status, series,
                                waterfall)

log = logging.getLogger(__name__)
DOMAIN_MODULE = {"sales": "sales", "pnl": "pnl", "bs": "bs", "cashflow": "cashflow", "inventory": "inventory"}
EXPLAIN_DIMS = {"sales": ["region", "org", "category", "channel"], "pnl": ["org"], "cashflow": ["cf_line", "org"],
                "bs": ["bs_line", "org"], "inventory": ["category"]}
METRIC_BLOCK = {"roe": "bs.dupont", "roa": "bs.dupont", "ccc": "bs.wc_cycle", "dso": "bs.wc_cycle",
                "dio": "bs.wc_cycle", "dpo": "bs.wc_cycle", "npat": "pnl.bridge", "pbt": "pnl.bridge",
                "ebitda": "pnl.waterfall", "net_cash_flow": "cashflow.waterfall", "cfo": "cashflow.waterfall",
                "inventory_cover": "inventory.slow_moving"}
PII = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"), (re.compile(r"(?<!\d)(?:\+?84|0)\d{9,10}(?!\d)"), "[sđt]")]


# ------------------------------------------------------------------ tiện ích
def redact(q: str) -> str:
    """redact_pii (Blueprint 14.4): xoá email, số điện thoại khỏi câu hỏi."""
    q = q.strip()[:500]
    for rx, rep in PII:
        q = rx.sub(rep, q)
    return q


def _latest() -> tuple[int, int]:
    y, m = db.meta()["last_closed_month"].split("-")
    return int(y), int(m)


def _period(p: dict | None, ctx: dict) -> Period:
    p = p or {}
    ly, lm = _latest()
    y, m = int(p.get("year") or ctx.get("year") or ly), int(p.get("month") or ctx.get("month") or lm)
    if (y, m) > (ly, lm):
        y, m = ly, lm
    return make(y, max(1, min(12, m)), p.get("mode") or ctx.get("mode") or "month")


def _module_of(key: str) -> str:
    return DOMAIN_MODULE[metric(key)["domain"]]


def ratio_period(key: str, p: Period) -> Period:
    """Chỉ số hiệu quả trên Bảng cân đối (ROE, DSO…) tính trên 12 tháng gần nhất – giống dashboard (Blueprint P2)."""
    m = metric(key)
    return p.trailing(12) if m["domain"] == "bs" and "formula" in m and ("months" in m["formula"] or "days" in m["formula"]
                                                                      or any(x in m["formula"] for x in ("dso", "dio", "dpo"))) else p


def _fmtv(key: str, v) -> str:
    return fmt.value(v, metric(key).get("format", "money"))


class Ctx:
    """Bộ nhớ đệm trong 1 câu hỏi: không dựng lại cùng một dashboard nhiều lần."""

    def __init__(self, user: User, filters: dict):
        self.user, self.filters, self.pages = user, filters, {}

    def page(self, mod: str, p: Period) -> dict:
        key = (mod, p)
        if key not in self.pages:
            from app.core.cache import cache
            u = self.user
            scope = "*" if u.is_all else ",".join(sorted(u.orgs or []))
            ck = ("page", mod, p, repr(sorted(self.filters.items())), scope, tuple(u.modules))
            self.pages[key] = cache.get(ck, lambda: REGISTRY[mod].build(p, u, self.filters))
        return self.pages[key]


# ------------------------------------------------------------------ ④ kiểm phiếu
def validate(part: dict, user: User) -> tuple[dict, list[str]]:
    m = model()
    notes = []
    part["metrics"] = [k for k in part.get("metrics", []) if k in m["metrics"] and not m["metrics"][k].get("internal")]
    if "plan" in part.get("compare", []) and "plan_revenue" in part["metrics"] and len(part["metrics"]) > 1:
        part["metrics"].remove("plan_revenue")
    part["dims"] = [d for d in part.get("dims", []) if d in m["dims"]][:1]
    cat = catalog()
    clean = {}
    for d, vals in (part.get("filters") or {}).items():
        if d not in cat:
            continue
        ok = [v for v in vals if v in cat[d]]
        if len(ok) < len(vals):
            notes.append(f"Bỏ qua giá trị lọc không có trong dữ liệu: {', '.join(set(vals) - set(ok))}")
        if ok:
            clean[d] = ok
    if not user.is_all:  # đơn vị/khu vực ngoài phạm vi -> chặn và báo rõ, không trả số (Blueprint 14.3)
        orgs = db.fetch("SELECT org_code, org_name, region_name FROM bi_org")
        mine = [o for o in orgs if o["org_code"] in (user.orgs or set())]
        allowed = {"org": {o["org_name"] for o in mine}, "region": {o["region_name"] for o in mine}}
        for d in ("org", "region"):
            out = [v for v in clean.get(d, []) if v not in allowed[d]]
            if out:
                notes.append(f"Ngoài phạm vi dữ liệu của bạn: {', '.join(out)}")
                part["out_of_scope"] = True
                log.warning("SCOPE: user=%s hỏi ngoài phạm vi %s", user.username, out)
    part["filters"] = clean
    if part.get("block") and part["block"].split(".")[0] not in REGISTRY:
        part["block"] = None
    denied = [k for k in part["metrics"] if not user.can(_module_of(k))]
    if part.get("block") and not user.can(part["block"].split(".")[0]):
        denied.append(part["block"])
        part["block"] = None
    if denied:
        lab = lambda k: metric(k)["label"] if k in m["metrics"] else k  # noqa: E731
        pro = [k for k in denied if (k in m["metrics"] and _module_of(k) in config.LOCKED_MODULES)
               or k.split(".")[0] in config.LOCKED_MODULES]
        if pro:  # bản Lite: lời mời nâng cấp thay cho báo thiếu quyền
            notes.append("🔒 " + ", ".join(lab(k) for k in pro) + " thuộc bản **AI4BI Pro** "
                         f"(Kết quả kinh doanh, Bảng cân đối, Lưu chuyển tiền, Tồn kho). Xem: {config.UPGRADE_URL}")
        from app.core.dataset import has_data
        mod_of = lambda k: _module_of(k) if k in m["metrics"] else k.split(".")[0]  # noqa: E731
        nodata = [k for k in denied if k not in pro and not has_data(mod_of(k))]
        if nodata:  # khách mới nạp một phần dữ liệu: nói rõ thiếu gì, không nói "chưa được cấp quyền"
            notes.append("Chưa có dữ liệu cho: " + ", ".join(lab(k) for k in nodata)
                         + ". Nạp thêm sheet tương ứng ở trang Dữ liệu của tôi.")
        rest = [k for k in denied if k not in pro and k not in nodata]
        if rest:
            notes.append("Bạn chưa được cấp quyền xem: " + ", ".join(lab(k) for k in rest))
        part["metrics"] = [k for k in part["metrics"] if k not in denied]
    return part, notes


# ------------------------------------------------------------------ ⑥ thực thi từng loại câu hỏi
def do_value(part: dict, p: Period, c: Ctx) -> dict:
    keys = part["metrics"]
    f = {**c.filters, **part["filters"]}
    want_plan = "plan" in part["compare"]
    cur = total(keys, p, c.user, f, plan=want_plan)
    yoy = total(keys, p.yoy(), c.user, f)
    prv = total(keys, p.prev(), c.user, f)
    for k in keys:
        rp = ratio_period(k, p)
        if rp != p:
            cur[k] = total([k], rp, c.user, f).get(k)
            yoy[k] = total([k], rp.yoy(), c.user, f).get(k)
            prv[k] = total([k], rp.shift(-1), c.user, f).get(k)
    kpis, facts, lines = [], [], []
    for k in keys:
        comps = [compare(k, cur.get(k), yoy.get(k), "Cùng kỳ"), compare(k, cur.get(k), prv.get(k), "Kỳ trước")]
        if want_plan and cur.get(f"{k}__plan") is not None:
            comps.append(compare(k, cur[k], cur[f"{k}__plan"], "Kế hoạch"))
        kp = kpi(k, cur.get(k), comps)
        kpis.append(kp)
        if cur.get(k) is None:
            lines.append(f"- **{metric(k)['label']}**: chưa có dữ liệu cho {p.label}.")
            continue
        plabel = "12 tháng đến " + p.end_month().label if ratio_period(k, p) != p else p.label
        s = f"- **{metric(k)['label']}** {plabel}: **{kp['display']}**"
        extra = [f"{x['delta_display']} so {x['label'].lower()} ({x['base_display']})" for x in comps if x.get("delta") is not None]
        if extra:
            s += " – " + "; ".join(extra)
        tone = kp.get("tone")
        if tone in ("good", "warn", "bad"):
            s += " · " + {"good": "mức tốt", "warn": "cần theo dõi", "bad": "dưới ngưỡng an toàn"}[tone]
        lines.append(s + ".")
        facts.append(s.replace("**", "").lstrip("- "))
    if want_plan and "net_revenue" in keys:
        pl = sales_mod.plan_by(p, c.user, f, None).get("_all")
        if pl:
            tone, status = plan_status(pl["ratio"])
            kpis.append({"key": "plan_attainment", "label": "% Kế hoạch (kênh Đại lý)", "value": pl["ratio"], "format": "pct",
                         "display": fmt.pct(pl["ratio"]), "tone": tone, "compares": [],
                         "sub": f"{status} · TH {fmt.money(pl['actual'])} / KH {fmt.money(pl['plan'])}"})
            s = (f"Kênh Đại lý đạt **{fmt.pct(pl['ratio'])} kế hoạch** ({fmt.money(pl['actual'])} / {fmt.money(pl['plan'])}) – "
                 f"{status.lower()}.")
            lines.append("- " + s)
            facts.append(s.replace("**", ""))
        else:
            lines.append("- Kỳ này chưa có kế hoạch doanh thu để so sánh.")
    blocks = []
    k0 = keys[0] if keys else None
    if k0 and cur.get(k0) is not None:
        tr = p.trailing(12)
        rows = {r["month"]: r for r in run(Query([k0], ["month"], tr, f), c.user).rows}
        yms = months_of(tr)
        blocks.append(chart("trend", f"{metric(k0)['label']} 12 tháng gần nhất", "line" if metric(k0).get("format") != "money" else "bar",
                            month_labels(yms), [series(metric(k0)["label"], [rows.get(m, {}).get(k0) for m in yms])],
                            fmt_=metric(k0).get("format", "money"), width="full"))
    return {"text": "\n".join(lines), "kpis": kpis, "blocks": blocks, "facts": facts, "sql": cur["_sql"],
            "notes": cur["_notes"]}


def do_breakdown(part: dict, p: Period, c: Ctx, rank: bool = False) -> dict:
    d = part["dims"][0] if part["dims"] else "org"
    keys = part["metrics"][:3]
    f = {**c.filters, **part["filters"]}
    res = run(Query(keys, [d], p, f), c.user)
    if not res.rows or all(r.get(keys[0]) is None for r in res.rows):
        return {"text": "Không có dữ liệu phù hợp. " + " ".join(res.notes), "kpis": [], "blocks": [], "facts": [],
                "sql": res.sql, "notes": res.notes}
    k0 = keys[0]
    kfmt = metric(k0).get("format", "money")
    ly = {r[d]: r.get(k0) for r in run(Query([k0], [d], p.yoy(), f), c.user).rows}
    additive = "formula" not in metric(k0) and kfmt in ("money", "number")
    tot = sum(r.get(k0) or 0 for r in res.rows) if additive else None
    rows = [dict(r) for r in res.rows if r.get(k0) is not None]
    for r in rows:
        r["ly"] = ly.get(r[d])
        r["diff"] = r[k0] - r["ly"] if r["ly"] is not None else None
        r["share"] = (r[k0] / tot) if additive and tot else None
        r["growth"] = fmt.growth(r[k0], r["ly"]) if kfmt != "pct" else None
        r["_tone"] = {"growth": "good" if (r["growth"] or 0) >= 0 else "bad",
                      "diff": "good" if (r["diff"] or 0) >= 0 else "bad"}
    want_plan = "plan" in part["compare"] and k0 == "net_revenue" and d in ("org", "region", "channel")
    if want_plan:
        pl = sales_mod.plan_by(p, c.user, f, d)
        for r in rows:
            x = pl.get(r[d], {})
            r["plan"], r["ratio"] = x.get("plan"), x.get("ratio")
            tone, r["status"] = plan_status(x.get("ratio"))
            r["_tone"]["status"] = tone
    asc = part.get("order") == "asc"
    if rank or want_plan or not (dim(d).get("order")):
        key = (lambda r: (r["ratio"] is None, r["ratio"] or 0)) if want_plan else (lambda r: r[k0] or 0)
        rows.sort(key=key, reverse=not (asc or want_plan))
    if rank:
        rows = rows[:part.get("top_n") or 5]
    # Một khối duy nhất: biểu đồ kỳ này + cùng kỳ + % tăng trưởng; nút "Bảng" mở đủ số so sánh (không lặp bảng riêng)
    has_ly = kfmt != "pct" and any(r["ly"] is not None for r in rows)
    cols = [col(d, dim(d)["label"]), col(k0, p.label, kfmt)]
    if has_ly:
        cols += [col("ly", f"Cùng kỳ {p.yoy().label}", kfmt), col("diff", "Chênh lệch", kfmt, True),
                 col("growth", "Tăng trưởng", "signed_pct", True)]
    cols += [col(k, metric(k)["label"], metric(k).get("format", "money")) for k in keys[1:]]
    if additive:
        cols.append(col("share", "Tỷ trọng", "pct"))
    if want_plan:
        cols += [col("plan", "KH Đại lý", "money"), col("ratio", "% KH", "pct"), col("status", "Đánh giá", "badge", True)]
    title = f"{metric(k0)['label']} theo {dim(d)['label'].lower()} – {p.label}"
    shown = rows[:15]
    kind = "hbar" if rank or len(shown) > 6 or max(len(str(r[d])) for r in shown) > 14 else "bar"
    ser = [series(p.label, [r[k0] for r in shown])]
    if has_ly:
        ser.append(series(f"Cùng kỳ {p.yoy().label}", [r["ly"] for r in shown], color="muted"))
    blk = chart("chart", title, kind, [r[d] for r in shown], ser, fmt_=kfmt)
    blk["growth"] = [r["growth"] for r in shown] if has_ly else None
    blk["table"] = {"columns": cols, "rows": rows[:30]}
    if kind == "hbar":
        blk["height"] = max(240, len(shown) * (34 if has_ly else 24) + 70)
    blocks = [blk]
    facts, lines = [], []
    head = rows[:3]
    if additive and not rank:
        lines.append(f"**{title}** – tổng **{_fmtv(k0, tot)}**.")
    for r in head:
        s = f"{r[d]}: {_fmtv(k0, r[k0])}"
        if r.get("share") is not None:
            s += f" ({fmt.pct(r['share'])})"
        if r.get("growth") is not None:
            s += f", {fmt.signed_pct(r['growth'])} so cùng kỳ"
        if want_plan:
            s += f", đạt {fmt.pct(r.get('ratio'))} KH – {r['status'].lower()}"
        facts.append(s)   # chỉ làm evidence (nhật ký / làn sâu); không in lại vì biểu đồ đã có
    if want_plan:
        miss = [r for r in rows if r.get("ratio") is not None and r["ratio"] < 0.95]
        s = (f"Có {len(miss)}/{len([r for r in rows if r.get('ratio') is not None])} {dim(d)['label'].lower()} chưa đạt kế hoạch"
             + (f": {', '.join(r[d] for r in miss[:5])}." if miss else "."))
        lines.append(s)
        facts.append(s)
    elif len(rows) > 3 and not rank:
        g = [r for r in rows if r.get("growth") is not None]
        if g:
            best, worst = max(g, key=lambda r: r["growth"]), min(g, key=lambda r: r["growth"])
            s = (f"Tăng trưởng tốt nhất: {best[d]} ({fmt.signed_pct(best['growth'])}); "
                 f"kém nhất: {worst[d]} ({fmt.signed_pct(worst['growth'])}).")
            lines.append(s)
            facts.append(s)
    return {"text": "\n".join(lines), "kpis": [], "blocks": blocks, "facts": facts, "sql": res.sql, "notes": res.notes,
            "alias_dim": d if dim(d).get("sensitive") else None, "alias_values": [r[d] for r in rows]}


def do_trend(part: dict, p: Period, c: Ctx) -> dict:
    keys = part["metrics"][:3]
    f = {**c.filters, **part["filters"]}
    tr = Period(p.year, 1, p.m_to, "ytd") if p.mode == "ytd" and p.m_to >= 6 else p.trailing(12)
    yms = months_of(tr)
    cur = {r["month"]: r for r in run(Query(keys, ["month"], tr, f), c.user).rows}
    ly = {r["month"]: r for r in run(Query(keys[:1], ["month"], tr.yoy(), f), c.user).rows}
    k0 = keys[0]
    ser = [series(metric(k)["label"], [cur.get(m, {}).get(k) for m in yms]) for k in keys]
    ser.append(series(f"{metric(k0)['label']} cùng kỳ", [ly.get(m - 100, {}).get(k0) for m in yms], color="muted"))
    if all(v is None for s in ser for v in s["data"]):   # [trend-empty-v1] không vẽ khối trống
        return {"text": "\n".join(f"- **{metric(k)['label']}**: chưa có dữ liệu cho {tr.label}." for k in keys),
                "kpis": [], "blocks": [], "facts": [], "sql": [], "notes": []}
    blocks = [chart("trend", f"Diễn biến {metric(k0)['label'].lower()} {tr.label}", "line", month_labels(yms), ser,
                    fmt_=metric(k0).get("format", "money"), width="full")]
    vals = [(m, cur.get(m, {}).get(k0)) for m in yms if cur.get(m, {}).get(k0) is not None]
    facts, lines = [], [f"**Diễn biến {metric(k0)['label'].lower()} {tr.label}:**"]
    if vals:
        hi, lo = max(vals, key=lambda x: x[1]), min(vals, key=lambda x: x[1])
        avg = sum(v for _, v in vals) / len(vals)
        last = vals[-1]
        for s in (f"Cao nhất {month_labels([hi[0]])[0]}: {_fmtv(k0, hi[1])}; thấp nhất {month_labels([lo[0]])[0]}: {_fmtv(k0, lo[1])}.",
                  f"Bình quân tháng {_fmtv(k0, avg)}; tháng gần nhất {month_labels([last[0]])[0]}: {_fmtv(k0, last[1])} "
                  f"({'cao hơn' if last[1] >= avg else 'thấp hơn'} bình quân)."):
            lines.append(f"- {s}")
            facts.append(s)
        tot_cur = sum(v for _, v in vals)
        tot_ly = sum(ly.get(m - 100, {}).get(k0) or 0 for m, _ in vals)
        if "formula" not in metric(k0) and tot_ly:
            s = f"Tổng {len(vals)} tháng {_fmtv(k0, tot_cur)}, {fmt.signed_pct(fmt.growth(tot_cur, tot_ly))} so cùng kỳ."
            lines.append(f"- {s}")
            facts.append(s)
    return {"text": "\n".join(lines), "kpis": [], "blocks": blocks, "facts": facts, "sql": [], "notes": []}


def do_block(part: dict, p: Period, c: Ctx) -> dict:
    mod, bid = part["block"].split(".")
    pg = c.page(mod, p)
    blocks = [b for b in pg["blocks"] if b["id"] == bid]
    kp = [k for k in pg["kpis"] if k["key"] in part["metrics"]] or pg["kpis"][:4]
    facts = [i["text"] for i in pg["insights"]]
    title = blocks[0]["title"] if blocks else REGISTRY[mod].TITLE
    lines = [f"**{title}**"] + [f"- {t}" for t in facts[:4]]
    for k in kp[:4]:
        facts.append(f"{k['label']}: {k['display']}" + "".join(f"; {x['delta_display']} so {x['label'].lower()}"
                                                                 for x in k.get("compares", []) if x.get("delta") is not None))
    return {"text": "\n".join(lines), "kpis": kp, "blocks": [{**b, "width": "full"} for b in blocks], "facts": facts,
            "sql": pg.get("sql", []), "notes": pg.get("notes", [])}


def do_explain(part: dict, p: Period, c: Ctx) -> dict:
    k0 = part["metrics"][0] if part["metrics"] else None
    blk = part.get("block") or (METRIC_BLOCK.get(k0) if k0 else None)
    out = do_block({**part, "block": blk}, p, c) if blk else {"text": "", "kpis": [], "blocks": [], "facts": [], "sql": [], "notes": []}
    if not k0:
        return out
    f = {**c.filters, **part["filters"]}
    m = metric(k0)
    p = ratio_period(k0, p)
    base_label, base_p, use_plan = "cùng kỳ", p.yoy(), "plan" in part["compare"] and m.get("plan_sql")
    cur = total([k0], p, c.user, f, plan=bool(use_plan))
    base_v = cur.get(f"{k0}__plan") if use_plan else total([k0], base_p, c.user, f).get(k0)
    if use_plan:
        base_label = "kế hoạch"
    lines = [f"**{m['label']} {p.label}: {_fmtv(k0, cur.get(k0))}** so {base_label} {_fmtv(k0, base_v)}"
             + (f" ({fmt.signed_pct(fmt.growth(cur.get(k0), base_v))})" if m.get("format") != "pct" else
                f" ({fmt.pp((cur.get(k0) or 0) - (base_v or 0))})") + "."]
    facts = [lines[0].replace("**", "")]
    if "formula" in m:   # chỉ tiêu tỷ lệ: phân rã theo thành phần
        from app.core.semantic import _deps
        comps = sorted(_deps(m["formula"]) - {"months", "days"})
        cc, bb = total(comps, p, c.user, f), total(comps, base_p, c.user, f)
        lines.append(f"Công thức: {m['label']} = `{m['formula']}`. Thành phần:")
        for k in comps:
            s = f"{metric(k)['label']}: {_fmtv(k, cc.get(k))} so {_fmtv(k, bb.get(k))} cùng kỳ ({fmt.signed_pct(fmt.growth(cc.get(k), bb.get(k)))})"
            lines.append(f"- {s}")
            facts.append(s)
    elif m.get("source"):
        dims = [d for d in EXPLAIN_DIMS.get(m["domain"], ["org"]) if model()["dims"][d]["columns"].get(m["source"])]
        best_dim, best_rows = None, []
        for d in dims:
            if d in part["filters"]:
                continue
            try:
                a = {r[d]: r.get(k0) or 0 for r in run(Query([k0], [d], p, f), c.user).rows}
                b = {r[d]: r.get(k0) or 0 for r in run(Query([k0], [d], base_p, f), c.user).rows}
            except SemanticError:
                continue
            deltas = sorted(((x, a.get(x, 0) - b.get(x, 0)) for x in set(a) | set(b)), key=lambda t: -abs(t[1]))
            if not deltas:
                continue
            pos = [x for x in deltas if x[1] > 0][:3]
            neg = [x for x in deltas if x[1] < 0][:3]
            s = f"Theo {dim(d)['label'].lower()}: " + "; ".join(
                [f"kéo tăng {x} ({fmt.money(v) if m.get('format') == 'money' else _fmtv(k0, v)})" for x, v in pos] +
                [f"kéo giảm {x} ({fmt.money(v) if m.get('format') == 'money' else _fmtv(k0, v)})" for x, v in neg])
            lines.append(f"- {s}.")
            facts.append(s)
            if best_dim is None:
                best_dim, best_rows = d, deltas
        if best_dim and not out["blocks"]:
            steps = [(f"{m['label']} {base_label}", base_v, "total")] + [(x, v, "delta") for x, v in best_rows[:8]]
            rest = sum(v for _, v in best_rows[8:])
            if abs(rest) > 0:
                steps.append(("Khác", rest, "delta"))
            steps.append((f"{m['label']} kỳ này", cur.get(k0), "total"))
            out["blocks"].append(waterfall("explain", f"Đóng góp vào thay đổi {m['label'].lower()} theo {dim(best_dim)['label'].lower()}",
                                           steps, width="full"))
    out["text"] = "\n".join(lines) + ("\n\n" + out["text"] if out["text"] else "")
    out["facts"] = facts + out["facts"]
    return out


def _run_part(p: dict, per: Period, c: Ctx) -> dict:
    intent = p["intent"]
    if intent == "block" and p.get("block"):
        return do_block(p, per, c)
    if intent == "explain":
        return do_explain(p, per, c)
    if intent == "trend":
        return do_trend(p, per, c)
    if intent in ("breakdown", "rank") or p.get("dims"):
        return do_breakdown(p, per, c, rank=intent == "rank")
    return do_value(p, per, c)


def _cached_part(p: dict, per: Period, c: Ctx) -> dict:
    """result_cache (Blueprint 10.4): khoá = phiếu đã kiểm + kỳ + bộ lọc + phạm vi user + phiên bản dữ liệu.
    Bấm lại cùng câu gợi ý -> trả ngay, không chạy lại SQL."""
    from app.core.cache import cache
    u = c.user
    scope = "*" if u.is_all else ",".join(sorted(u.orgs or []))
    ck = ("chat", p["intent"], tuple(p.get("metrics") or []), tuple(p.get("dims") or []), p.get("block"),
          repr(sorted((p.get("filters") or {}).items())), tuple(p.get("compare") or []), p.get("top_n"), p.get("order"),
          per, repr(sorted(c.filters.items())), scope, tuple(u.modules))
    r = cache.get(ck, lambda: _run_part(p, per, c))
    return {**r, "kpis": list(r["kpis"]), "blocks": list(r["blocks"]), "facts": list(r["facts"])}


# ------------------------------------------------------------------ gợi ý câu tiếp theo
def suggestions(parts: list[dict], user: User) -> list[str]:
    s = []
    for p in parts:
        k = (p.get("metrics") or [None])[0]
        if k:
            lab = metric(k)["label"].lower()
            if p["intent"] != "explain":
                s.append(f"Vì sao {lab} thay đổi so với cùng kỳ?")
            if p["intent"] != "trend":
                s.append(f"Diễn biến {lab} 12 tháng gần nhất")
            if p["intent"] not in ("breakdown", "rank") and metric(k).get("source") == "sales":
                s.append(f"{metric(k)['label']} theo khu vực và mức đạt kế hoạch")
    fc = db.meta().get("focus_channel")
    base = {"sales": f"Top 10 {fc.lower() if fc else 'khách hàng'} doanh thu cao nhất quý này", "pnl": "Vì sao lợi nhuận sau thuế thay đổi?",
            "bs": "Phân rã ROE theo DuPont", "cashflow": "Tiền trong kỳ tăng giảm vì những khoản nào?",
            "inventory": "Hàng nào chậm luân chuyển?"}
    s += [q for mod, q in base.items() if user.can(mod)]
    out = []
    for x in s:
        if x not in out:
            out.append(x)
    return out[:4]


def welcome(user: User) -> dict:
    extra = ["Doanh thu quý này so với cùng kỳ và kế hoạch",
             "Biên lợi nhuận gộp theo tháng" if user.can("pnl") else "Doanh thu theo kênh bán 12 tháng gần nhất"]
    return {"company": db.meta().get("company_name"), "suggestions": (suggestions([], user) + extra)[:6]}


HELP = ("Tôi là **Trợ lý AI4BI** của {company}. Tôi trả lời trên đúng bộ số của dashboard: "
        "**Doanh thu, P&L, Bảng cân đối, Lưu chuyển tiền tệ, Tồn kho**.\n\n"
        "Bạn có thể hỏi:\n- Con số: *Doanh thu quý 3 so với cùng kỳ*\n- Tách theo chiều: *Lợi nhuận theo đơn vị năm 2025*\n"
        "- Xếp hạng: *Top 5 đại lý doanh thu cao nhất*\n- Xu hướng: *Dòng tiền kinh doanh theo tháng*\n"
        "- Nguyên nhân: *Vì sao lợi nhuận giảm?*\n- Khối dashboard: *Cơ cấu tài sản*, *Chu kỳ tiền mặt*, *Hàng chậm luân chuyển*")


HELP_LITE = ("Tôi là **Trợ lý AI4BI Lite** của {company}. Tôi trả lời trên đúng bộ số của dashboard "
             "**Tổng quan** và **Doanh thu**.\n\n"
             "Bạn có thể hỏi:\n- Con số: *Doanh thu quý 3 so với cùng kỳ*\n"
             "- Tách theo chiều: *Doanh thu theo khu vực năm 2025*\n"
             "- Xếp hạng: *Top 5 đại lý doanh thu cao nhất*\n- Xu hướng: *Doanh thu theo tháng*\n"
             "- Nguyên nhân: *Vì sao doanh thu giảm?*\n\n"
             "🔒 P&L, Bảng cân đối, Lưu chuyển tiền, Tồn kho có ở bản **AI4BI Pro**: {url}")


# ------------------------------------------------------------------ luồng chính
def answer(question: str, ctx: dict, user: User, history: list[str] | None = None, last: dict | None = None) -> dict:
    t0 = time.time()
    if not user.can("chat"):
        return {"answer": "Bạn chưa được cấp quyền dùng Trợ lý AI.", "blocks": [], "kpis": [], "suggestions": [],
                "status": "denied"}
    q = redact(question)
    ly, lm = _latest()
    ctx = {**ctx, "latest_year": ly, "latest_month": lm}
    company = db.meta().get("company_name", "doanh nghiệp")
    parser, parts = "rules", None
    if not config.PRO:
        # Bản Lite: hiểu câu bằng LUẬT trước cho nhanh; chỉ gọi LLM khi luật không hiểu câu.
        # Bản Pro / Enterprise: LLM hiểu câu theo Blueprint 12.2 khối ③ (luật là dự phòng khi LLM lỗi).
        parts = nlu.parse(q, ctx)
        if all(p["intent"] == "unknown" or (p["intent"] == "followup" and not last) for p in parts):
            parts = None   # luật không hiểu -> nhờ LLM (nếu có khoá)
    if parts is None and config.llm_on():
        try:
            from app.chat import llm
            from app.core.cache import cache
            # Phiếu của cùng câu hỏi + cùng ngữ cảnh kỳ + cùng 2 câu trước -> dùng lại, không gọi lại LLM hiểu câu
            hk = tuple((history or [])[-2:])
            ck = ("qform", q.lower(), ctx.get("year"), ctx.get("month"), ctx.get("mode"), ly, lm, hk)
            parts = [dict(x) for x in cache.get(ck, lambda: llm.parse_qform(q, ctx, list(hk)))]
            parser = "llm"
        except Exception as e:  # LLM lỗi -> luật; chi tiết chỉ vào log (Blueprint P12)
            log.warning("LLM parse lỗi: %s", e)
    if not parts:
        parts = nlu.parse(q, ctx)
    # câu nối tiếp ("còn quý 2 thì sao?") kế thừa phiếu trước
    for p in parts:
        if p["intent"] in ("followup", "unknown") and last and (p.get("filters") or p.get("period", {}).get("explicit")):
            p.update({k: last.get(k) for k in ("intent", "metrics", "dims", "block", "compare", "top_n", "order")})
            p["filters"] = {**(last.get("filters") or {}), **(p.get("filters") or {})}
    if parts[0]["intent"] in ("greeting", "help") or all(p["intent"] in ("unknown", "followup") for p in parts):
        msg = HELP.format(company=company) if config.PRO else HELP_LITE.format(company=company, url=config.UPGRADE_URL)
        if parts[0]["intent"] not in ("greeting", "help"):
            msg = "Tôi chưa hiểu rõ câu hỏi hoặc câu hỏi nằm ngoài dữ liệu đang có. " + msg
        return {"answer": msg, "blocks": [], "kpis": [], "suggestions": suggestions([], user), "parts": parts,
                "parser": parser, "lane": "fast", "elapsed_ms": int((time.time() - t0) * 1000), "status": "help"}
    c = Ctx(user, {k: v for k, v in (ctx.get("filters") or {}).items() if v})
    texts, kpis, blocks, facts, sqls, notes, aliases = [], [], [], [], [], [], {}
    for p in parts:
        p, n = validate(p, user)
        notes += n
        if p.get("out_of_scope"):
            texts.append("Câu hỏi chạm đơn vị nằm ngoài phạm vi dữ liệu được cấp cho bạn, nên tôi không trả lời phần này.")
            continue
        if not p["metrics"] and not p.get("block"):
            continue
        per = _period(p.get("period"), ctx)
        p["period_label"] = per.label
        try:
            r = _cached_part(p, per, c)
        except SemanticError as e:
            r = {"text": f"Không trả lời được phần này: {e}.", "kpis": [], "blocks": [], "facts": [], "sql": [], "notes": []}
        texts.append(r["text"])
        kpis += r["kpis"]
        blocks += r["blocks"]
        facts += r["facts"]
        sqls += r.get("sql", [])
        notes += r.get("notes", [])
        if r.get("alias_dim"):
            aliases.update({v: f"KH_{i + 1:02d}" for i, v in enumerate(r["alias_values"]) if v not in aliases})
    status = "out_of_scope" if any(p.get("out_of_scope") for p in parts) else "ok" if facts or kpis or blocks else "no_data"
    pro_note = next((n for n in notes if n.startswith("🔒")), None)  # bản Lite: hỏi chỉ số Pro
    template = "\n\n".join(t for t in texts if t)
    if not template and not (kpis or blocks):
        template = pro_note.removeprefix("🔒 ") if pro_note else "Không có dữ liệu phù hợp với câu hỏi."
    final, detail, lane, gate_errors = template, None, "fast", []
    # Blueprint 12.3: làn nhanh = 1 ý, có template, không hỏi "vì sao" -> KHÔNG gọi LLM chuyên gia.
    # Làn sâu (chuyên gia + gate) chỉ khi nhiều ý hoặc hỏi nguyên nhân.
    deep = config.LLM_NARRATIVE and facts and (
        len(parts) > 1 or any(p["intent"] == "explain" for p in parts)) and config.llm_on()
    if deep:
        lane = "deep"
        ev = facts[:]
        for real, alias in aliases.items():  # che tên khách hàng trước khi gửi LLM (Blueprint P5)
            ev = [x.replace(real, alias) for x in ev]
        try:
            from app.chat import llm
            text = llm.narrative(company, q, ev)
            gate_errors = gate.check(text, " ".join(ev), " ".join(p.get("period_label", "") for p in parts))
            if gate_errors:
                log.info("Gate chặn lời văn LLM: %s", gate_errors)
            else:
                for real, alias in aliases.items():
                    text = text.replace(alias, real)
                final, detail = text, template
        except Exception as e:
            log.warning("LLM narrative lỗi: %s", e)
    return {"answer": final, "detail": detail, "kpis": kpis, "blocks": blocks, "notes": sorted(set(notes)), "sql": sqls,
            "suggestions": suggestions(parts, user), "parts": [{k: v for k, v in p.items() if k != "question"} for p in parts],
            "parser": parser, "lane": lane, "gate": gate_errors, "elapsed_ms": int((time.time() - t0) * 1000),
            "status": status, "facts": facts}

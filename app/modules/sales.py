"""Phân hệ KINH DOANH – doanh thu, kế hoạch, kênh, ngành hàng, khách hàng, sản phẩm."""
from __future__ import annotations

from app.core import fmt
from app.core.periods import Period
from app.core.scope import User
from app.core.semantic import Query, run, total
from app.modules.common import (chart, col, compare, kpi, month_labels, months_of, page, plan_status,
                                safe_div, series, table, waterfall)

TITLE = "Kinh doanh"
KEYWORDS = {
    "trend": ["xu hướng", "theo tháng", "từng tháng", "biến động", "diễn biến"],
    "by_org": ["kế hoạch theo đơn vị", "bảng điểm", "đơn vị", "chi nhánh", "khu vực", "đạt kế hoạch"],
    "by_category": ["cơ cấu doanh thu", "ngành hàng", "nhóm hàng", "tỷ trọng"],
    "by_channel": ["kênh", "online", "đại lý"],
    "top_customers": ["top đại lý", "top khách hàng", "khách hàng lớn", "đại lý lớn nhất"],
    "top_products": ["top sản phẩm", "sản phẩm bán chạy", "mặt hàng chủ lực"],
    "growth_bridge": ["đóng góp tăng trưởng", "vì sao doanh thu", "tại sao doanh thu", "nguyên nhân doanh thu"],
}


def plan_by(period: Period, user: User, filters: dict, by: str | None) -> dict:
    """% kế hoạch chỉ so trên các cặp (đơn vị × kênh) có giao kế hoạch – không so doanh thu
    toàn bộ với kế hoạch của một kênh. by: None | region | org | channel."""
    dims = ["region", "org", "channel"]
    act = {(r["org"], r["channel"]): r for r in run(Query(["net_revenue"], dims, period, filters), user).rows}
    out: dict = {}
    for r in run(Query(["plan_revenue"], dims, period, filters), user).rows:
        if not r.get("plan_revenue"):
            continue
        slot = out.setdefault(r[by] if by else "_all", {"actual": 0.0, "plan": 0.0})
        slot["plan"] += r["plan_revenue"]
        slot["actual"] += (act.get((r["org"], r["channel"])) or {}).get("net_revenue") or 0
    for v in out.values():
        v["ratio"] = safe_div(v["actual"], v["plan"])
    return out


def build(period: Period, user: User, filters: dict) -> dict:
    keys = ["net_revenue", "sales_gross_profit", "sales_margin", "orders", "active_customers",
            "avg_order_value", "discount_rate", "quantity"]
    cur = total(keys, period, user, filters)
    yoy = total(keys, period.yoy(), user, filters)
    prv = total(keys, period.prev(), user, filters)
    pl = plan_by(period, user, filters, None).get("_all", {"actual": 0, "plan": 0, "ratio": None})
    tone, status = plan_status(pl["ratio"])
    plan_kpi = {"key": "plan_attainment", "label": "% Kế hoạch (kênh Đại lý)", "value": pl["ratio"], "format": "pct",
                "display": fmt.pct(pl["ratio"]), "tone": tone, "sub": f"{status} · KH {fmt.money(pl['plan'])}",
                "compares": [], "ask": "Đơn vị nào chưa đạt kế hoạch doanh thu?"}
    kpis = [
        kpi("net_revenue", cur["net_revenue"], [compare("net_revenue", cur["net_revenue"], yoy["net_revenue"], "Cùng kỳ"),
                                                compare("net_revenue", cur["net_revenue"], prv["net_revenue"], "Kỳ trước")],
            ask="Vì sao doanh thu thay đổi so với cùng kỳ?"),
        plan_kpi,
        kpi("sales_gross_profit", cur["sales_gross_profit"],
            [compare("sales_gross_profit", cur["sales_gross_profit"], yoy["sales_gross_profit"], "Cùng kỳ")],
            sub=f"Biên {fmt.pct(cur['sales_margin'])}"),
        kpi("orders", cur["orders"], [compare("orders", cur["orders"], yoy["orders"], "Cùng kỳ")]),
        kpi("active_customers", cur["active_customers"],
            [compare("active_customers", cur["active_customers"], yoy["active_customers"], "Cùng kỳ")]),
        kpi("avg_order_value", cur["avg_order_value"],
            [compare("avg_order_value", cur["avg_order_value"], yoy["avg_order_value"], "Cùng kỳ")]),
    ]
    blocks, insights = [], []

    # xu hướng: các tháng năm nay vs năm trước + kế hoạch
    ytd = Period(period.year, 1, period.m_to, "ytd")
    yms = months_of(Period(period.year, 1, 12, "ytd"))
    cur_m = {r["month"]: r for r in run(Query(["net_revenue"], ["month"], ytd, filters), user).rows}
    ly_m = {r["month"]: r for r in run(Query(["net_revenue"], ["month"], Period(period.year - 1, 1, 12, "ytd"), filters), user).rows}
    pl_m = {r["month"]: r for r in run(Query(["plan_revenue"], ["month"], Period(period.year, 1, 12, "ytd"), filters), user).rows}
    blocks.append(chart("trend", f"Doanh thu theo tháng {period.year} so với {period.year - 1}", "combo", month_labels(yms), [
        series(f"Năm {period.year}", [cur_m.get(m, {}).get("net_revenue") for m in yms], "bar"),
        series(f"Năm {period.year - 1}", [ly_m.get(m - 100, {}).get("net_revenue") for m in yms], "line", color="muted"),
        series("Kế hoạch kênh Đại lý", [pl_m.get(m, {}).get("plan_revenue") for m in yms], "line", color="plan"),
    ], ask="Xu hướng doanh thu theo tháng năm nay so với năm trước", width="full"))

    # bảng điểm đơn vị
    org_cur = {r["org"]: r for r in run(Query(["net_revenue", "sales_margin"], ["org"], period, filters), user).rows}
    org_yoy = {r["org"]: r for r in run(Query(["net_revenue"], ["org"], period.yoy(), filters), user).rows}
    org_pl = plan_by(period, user, filters, "org")
    regions = {r["org"]: r["region"] for r in run(Query(["net_revenue"], ["region", "org"], period, filters), user).rows}
    rows = []
    for org, r in org_cur.items():
        p = org_pl.get(org, {})
        tone_, status_ = plan_status(p.get("ratio"))
        g = fmt.growth(r["net_revenue"], org_yoy.get(org, {}).get("net_revenue"))
        rows.append({"region": regions.get(org), "org": org, "revenue": r["net_revenue"], "growth": g,
                     "margin": r["sales_margin"], "plan": p.get("plan"), "ratio": p.get("ratio"),
                     "status": status_, "_tone": {"status": tone_, "growth": "good" if (g or 0) >= 0 else "bad"}})
    blocks.append(table("by_org", "Bảng điểm đơn vị", [
        col("region", "Khu vực"), col("org", "Đơn vị"), col("revenue", "Doanh thu", "money"),
        col("growth", "So cùng kỳ", "signed_pct", True), col("margin", "Biên lãi gộp", "pct"),
        col("plan", "KH Đại lý", "money"), col("ratio", "% KH", "pct"), col("status", "Đánh giá", "badge", True),
    ], rows, ask="Đơn vị nào chưa đạt kế hoạch và vì sao?", width="full",
        note="% KH so doanh thu kênh Đại lý với kế hoạch kênh Đại lý (kênh Online chưa giao kế hoạch)."))
    misses = [r for r in rows if r["ratio"] is not None and r["ratio"] < 0.95]
    if misses:
        w = min(misses, key=lambda r: r["ratio"])
        insights.append({"level": "bad", "text": f"{len(misses)} đơn vị chưa đạt kế hoạch; thấp nhất là {w['org']} "
                                                  f"({fmt.pct(w['ratio'])})."})
    hits = [r for r in rows if r["ratio"] is not None and r["ratio"] >= 1]
    if hits:
        b = max(hits, key=lambda r: r["ratio"])
        insights.append({"level": "good", "text": f"{len(hits)} đơn vị đạt kế hoạch; cao nhất {b['org']} ({fmt.pct(b['ratio'])})."})

    # ngành hàng & cầu nối tăng trưởng
    cat_cur = run(Query(["net_revenue", "sales_margin"], ["category"], period, filters), user).rows
    cat_yoy = {r["category"]: r["net_revenue"] for r in run(Query(["net_revenue"], ["category"], period.yoy(), filters), user).rows}
    cat_cur.sort(key=lambda r: -(r["net_revenue"] or 0))
    blocks.append(chart("by_category", "Cơ cấu doanh thu theo ngành hàng", "doughnut", [r["category"] for r in cat_cur],
                        [series("Doanh thu", [r["net_revenue"] for r in cat_cur])], ask="Cơ cấu doanh thu theo ngành hàng"))
    steps = [("Cùng kỳ", yoy["net_revenue"], "total")]
    for r in cat_cur:
        steps.append((r["category"], (r["net_revenue"] or 0) - (cat_yoy.get(r["category"]) or 0), "delta"))
    steps.append(("Kỳ này", cur["net_revenue"], "total"))
    blocks.append(waterfall("growth_bridge", "Đóng góp tăng trưởng theo ngành hàng", steps,
                            ask="Ngành hàng nào đóng góp nhiều nhất vào tăng trưởng doanh thu?"))
    if cur["net_revenue"] and yoy["net_revenue"]:
        top = max(steps[1:-1], key=lambda s: abs(s[1]))
        insights.append({"level": "good" if cur["net_revenue"] >= yoy["net_revenue"] else "bad",
                         "text": f"Doanh thu {period.label} {fmt.money(cur['net_revenue'])}, "
                                 f"{fmt.signed_pct(fmt.growth(cur['net_revenue'], yoy['net_revenue']))} so cùng kỳ; "
                                 f"{top[0]} đóng góp lớn nhất ({fmt.money(top[1])})."})

    # kênh
    ch_cur = run(Query(["net_revenue"], ["channel"], period, filters), user).rows
    ch_yoy = {r["channel"]: r["net_revenue"] for r in run(Query(["net_revenue"], ["channel"], period.yoy(), filters), user).rows}
    blocks.append(chart("by_channel", "Doanh thu theo kênh", "bar", [r["channel"] for r in ch_cur], [
        series("Kỳ này", [r["net_revenue"] for r in ch_cur]),
        series("Cùng kỳ", [ch_yoy.get(r["channel"]) for r in ch_cur], color="muted"),
    ], ask="So sánh doanh thu kênh Đại lý và Online"))

    # top đại lý, top sản phẩm
    cus = run(Query(["net_revenue"], ["customer"], period, {**filters, "channel": ["Đại lý"]}), user).rows
    cus = sorted(cus, key=lambda r: -(r["net_revenue"] or 0))[:10]
    blocks.append(chart("top_customers", "Top 10 đại lý theo doanh thu", "hbar", [r["customer"] for r in cus],
                        [series("Doanh thu", [r["net_revenue"] for r in cus])], ask="Top 10 đại lý doanh thu cao nhất"))
    tot = cur["net_revenue"] or 0
    if cus and tot:
        share = sum(r["net_revenue"] for r in cus) / tot
        insights.append({"level": "warn" if share > 0.4 else "info",
                         "text": f"Top 10 đại lý chiếm {fmt.pct(share)} doanh thu – "
                                 f"{'mức tập trung cao, cần theo dõi rủi ro khách hàng lớn' if share > 0.4 else 'mức tập trung chấp nhận được'}."})
    prod = run(Query(["net_revenue", "sales_margin", "quantity"], ["product"], period, filters), user).rows
    prod = sorted(prod, key=lambda r: -(r["net_revenue"] or 0))[:10]
    blocks.append(table("top_products", "Top 10 sản phẩm", [
        col("product", "Sản phẩm"), col("net_revenue", "Doanh thu", "money"), col("quantity", "Sản lượng", "number"),
        col("sales_margin", "Biên lãi gộp", "pct"),
    ], prod, ask="Top sản phẩm bán chạy và biên lãi gộp"))
    if cur["sales_margin"] is not None and yoy["sales_margin"] is not None:
        d = cur["sales_margin"] - yoy["sales_margin"]
        insights.append({"level": "good" if d >= 0 else "warn",
                         "text": f"Biên lãi gộp bán hàng {fmt.pct(cur['sales_margin'])} ({fmt.pp(d)} so cùng kỳ)."})
    return page("sales", TITLE, period, kpis, blocks, insights, sql=cur["_sql"])

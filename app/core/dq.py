"""Kiểm chất lượng dữ liệu trên bi_* – CHỈ ĐỌC, báo chứ không sửa (Blueprint 9.3).

Danh mục: mã trống, mã trùng, mã có trong fact mà thiếu danh mục, tên khớp ≥ 2 mã ("cần người quyết").
Độ phủ thuộc tính: khoá có thuộc tính trống – mới phát sinh trong tháng cuối kỳ ⚠️, tồn đọng ℹ️ kèm % doanh thu,
  thiếu cấp tổ chức luôn ❌.
Bất thường fact: ngày sau mốc dữ liệu, giá trị NULL, đơn vị có dữ liệu cũ (ngày cuối < mốc − 3 ngày / thiếu tháng cuối).
Không đưa tên khách hàng vào kết quả – chỉ mã.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.core import db
from app.core.periods import Period
from app.core.recon import FAIL, INFO, MAX_ROWS, OK, SKIP, WARN, Check, has, money, num, q

ORG_FACTS = ("bi_sales", "bi_sales_plan", "bi_pnl", "bi_cost_detail", "bi_bs", "bi_cashflow")
LINE_FACTS = (("bi_pnl", "bi_pnl_lines"), ("bi_cost_detail", "bi_pnl_lines"), ("bi_bs", "bi_bs_lines"),
              ("bi_cashflow", "bi_cf_lines"))
MONEY_COLS = {"bi_sales": ("net_revenue", "gross_amount", "discount_amount", "cogs", "quantity"),
              "bi_sales_plan": ("plan_revenue",), "bi_pnl": ("amount",), "bi_cost_detail": ("amount",),
              "bi_bs": ("amount",), "bi_cashflow": ("amount",), "bi_inventory": ("value_end", "units_end")}
COVERAGE = (  # (thuộc tính, cột, khoá theo dõi, nhãn)
    ("channel", "channel", "customer_code", "Kênh"),
    ("customer_type", "customer_type", "customer_code", "Loại khách hàng"),
    ("category", "category", "product_code", "Ngành hàng"),
    ("subcategory", "subcategory", "product_code", "Dòng sản phẩm"),
)
STALE_DAYS = 3


def _blank(col: str) -> str:
    return f"COALESCE(TRIM({col}), '') = ''"


def as_of() -> date | None:
    v = db.meta().get("as_of_date") or ""
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        r = q("SELECT MAX(CAST(date AS VARCHAR(10))) AS v FROM bi_sales")[0]["v"] if has("bi_sales") else None
        return date.fromisoformat(r[:10]) if r else None


# ------------------------------------------------------------------ danh mục
def catalog() -> list[Check]:
    L, out = "DQ danh mục", []
    if not has("bi_org"):
        return [Check(L, "Danh mục đơn vị (bi_org)", FAIL, "Không có danh mục đơn vị – thiếu cấp tổ chức")]
    blank = q(f"SELECT org_code, org_name FROM bi_org WHERE {_blank('org_code')} OR {_blank('org_name')}")
    dup = q("SELECT org_code, COUNT(*) AS n FROM bi_org GROUP BY org_code HAVING COUNT(*) > 1")
    same = q("SELECT org_name, COUNT(DISTINCT org_code) AS so_ma FROM bi_org GROUP BY org_name "
             "HAVING COUNT(DISTINCT org_code) > 1")
    out.append(Check(L, "Đơn vị: mã / tên trống, mã trùng", FAIL if blank or dup else OK,
                     f"{len(blank)} trống, {len(dup)} mã trùng" if blank or dup else "Không có", blank + dup))
    out.append(Check(L, "Đơn vị: một tên ứng với ≥ 2 mã (cần người quyết)", WARN if same else OK,
                     f"{len(same)} tên" if same else "Không có", same))
    rows = []
    for t in ORG_FACTS:
        if not has(t):
            continue
        col = "net_revenue" if t == "bi_sales" else ("plan_revenue" if t == "bi_sales_plan" else "amount")
        for r in q(f"""SELECT org_code, COUNT(*) AS so_dong, SUM({col}) AS so_tien, MIN(year*100+month) AS tu,
                              MAX(year*100+month) AS den FROM {t}
                       WHERE org_code IS NULL OR org_code NOT IN (SELECT org_code FROM bi_org)
                       GROUP BY org_code LIMIT {MAX_ROWS}"""):  # noqa: S608
            rows.append({"bang": t, **r})
    out.append(Check(L, "Mã đơn vị trong fact có trong danh mục đơn vị", FAIL if rows else OK,
                     f"{len(rows)} mã thiếu" if rows else "Đủ", rows))
    rows = []
    for t, lines in LINE_FACTS:
        if has(t) and has(lines):
            for r in q(f"SELECT line_code, COUNT(*) AS so_dong FROM {t} "  # noqa: S608
                       f"WHERE line_code IS NULL OR line_code NOT IN (SELECT line_code FROM {lines}) "
                       f"GROUP BY line_code LIMIT {MAX_ROWS}"):
                rows.append({"bang": t, "danh_muc": lines, **r})
    out.append(Check(L, "Mã khoản mục có trong danh mục dòng báo cáo", FAIL if rows else OK,
                     f"{len(rows)} mã thiếu" if rows else "Đủ", rows))
    if has("bi_sales"):
        for key, name, label in (("product_code", "product_name", "Sản phẩm"), ("customer_code", None, "Khách hàng")):
            n = q(f"SELECT COUNT(*) AS n, SUM(net_revenue) AS v FROM bi_sales WHERE {_blank(key)}")[0]  # noqa: S608
            cnt = int(n["n"] or 0)
            out.append(Check(L, f"{label}: mã trống trong bán hàng", WARN if cnt else OK,
                             f"{cnt} dòng, {money(num(n['v']))} đ" if cnt else "Không có"))
            multi = q(f"SELECT {key} AS ma, COUNT(DISTINCT {name or 'customer_name'}) AS so_ten FROM bi_sales "  # noqa: S608
                      f"WHERE NOT {_blank(key)} GROUP BY {key} HAVING COUNT(DISTINCT {name or 'customer_name'}) > 1 "
                      f"LIMIT {MAX_ROWS}")
            out.append(Check(L, f"{label}: một mã có ≥ 2 tên (cần người quyết)", WARN if multi else OK,
                             f"{len(multi)}{'+' if len(multi) == MAX_ROWS else ''} mã" if multi else "Không có", multi))
        same = q(f"SELECT product_name, COUNT(DISTINCT product_code) AS so_ma FROM bi_sales "
                 f"WHERE NOT {_blank('product_name')} GROUP BY product_name "
                 f"HAVING COUNT(DISTINCT product_code) > 1 LIMIT {MAX_ROWS}")
        out.append(Check(L, "Sản phẩm: một tên ứng với ≥ 2 mã (cần người quyết)", WARN if same else OK,
                         f"{len(same)}{'+' if len(same) == MAX_ROWS else ''} tên" if same else "Không có", same))
    return out


# ------------------------------------------------------------------ độ phủ thuộc tính
def coverage(period: Period) -> list[Check]:
    L, out = "DQ độ phủ", []
    if not has("bi_sales"):
        return [Check(L, "Độ phủ thuộc tính bán hàng", SKIP, "Chưa có dữ liệu bán hàng")]
    p = {"e": period.end}
    tot = num(q("SELECT SUM(net_revenue) AS v FROM bi_sales WHERE year*100+month <= :e", p)[0]["v"])
    no_org = q("""SELECT COUNT(*) AS n, SUM(net_revenue) AS v FROM bi_sales
                  WHERE year*100+month <= :e AND (org_code IS NULL OR org_code NOT IN (SELECT org_code FROM bi_org))""",
               p)[0]
    reg = q(f"SELECT org_code FROM bi_org WHERE {_blank('region_name')}") if has("bi_org") else []
    bad = int(no_org["n"] or 0) or reg
    out.append(Check(L, "Cấp tổ chức (đơn vị, khu vực)", FAIL if bad else OK,
                     f"{int(no_org['n'] or 0)} dòng bán hàng không có đơn vị hợp lệ, {len(reg)} đơn vị thiếu khu vực"
                     if bad else "Đủ", reg))
    for _, col, key, label in COVERAGE:
        rows = q(f"""SELECT {key} AS ma, MIN(year*100+month) AS tu, MAX(year*100+month) AS den,
                            COUNT(*) AS so_dong, SUM(net_revenue) AS doanh_thu FROM bi_sales
                     WHERE year*100+month <= :e AND {_blank(col)} GROUP BY {key}""", p)  # noqa: S608
        if not rows:
            out.append(Check(L, label, OK, "Đủ"))
            continue
        new = [r for r in rows if int(r["tu"]) >= period.start]
        rev = sum(num(r["doanh_thu"]) for r in rows)
        pct = rev / tot * 100 if tot else 0
        rows.sort(key=lambda r: -abs(num(r["doanh_thu"])))
        for r in rows:
            r["trang_thai"] = "mới" if int(r["tu"]) >= period.start else "tồn đọng"
        out.append(Check(L, label, WARN if new else INFO,
                         f"{len(rows)} mã trống {label.lower()} ({len(new)} mới trong {period.label}), "
                         f"{pct:.2f}% doanh thu", rows[:MAX_ROWS]))
    return out


# ------------------------------------------------------------------ bất thường fact
def anomalies(period: Period) -> list[Check]:
    L, out = "DQ bất thường", []
    mark = as_of()
    if has("bi_sales") and mark:
        fut = q(f"""SELECT order_no, CAST(date AS VARCHAR(10)) AS date, org_code, net_revenue FROM bi_sales
                    WHERE CAST(date AS VARCHAR(10)) > :d ORDER BY date DESC LIMIT {MAX_ROWS}""", {"d": mark.isoformat()})
        out.append(Check(L, f"Ngày chứng từ sau mốc dữ liệu {mark:%d/%m/%Y}", WARN if fut else OK,
                         f"{len(fut)}{'+' if len(fut) == MAX_ROWS else ''} dòng" if fut else "Không có", fut))
        nod = int(q("SELECT COUNT(*) AS n FROM bi_sales WHERE date IS NULL")[0]["n"] or 0)
        if nod:
            out.append(Check(L, "Ngày chứng từ trống", WARN, f"{nod} dòng"))
    rows = []
    for t, cols in MONEY_COLS.items():
        if not has(t):
            continue
        sel = ", ".join(f"SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS {c}" for c in cols)
        r = q(f"SELECT {sel} FROM {t}")[0]  # noqa: S608
        rows += [{"bang": t, "cot": c, "so_dong_null": int(r[c])} for c in cols if r[c]]
    out.append(Check(L, "Giá trị NULL ở cột số", WARN if rows else OK,
                     f"{len(rows)} cột có NULL" if rows else "Không có", rows))
    rows = []
    if has("bi_sales") and has("bi_org") and mark:
        limit = (mark - timedelta(days=STALE_DAYS)).isoformat()
        for r in q("""SELECT o.org_code, MAX(CAST(s.date AS VARCHAR(10))) AS ngay_cuoi FROM bi_org o
                      LEFT JOIN bi_sales s ON s.org_code = o.org_code GROUP BY o.org_code"""):
            if r["ngay_cuoi"] and r["ngay_cuoi"] < limit:
                rows.append({"bang": "bi_sales", **r})
    for t in ORG_FACTS[1:]:
        if has(t) and has("bi_org"):
            last = int(q(f"SELECT MAX(year*100+month) AS v FROM {t}")[0]["v"] or 0)  # noqa: S608
            for r in q(f"""SELECT o.org_code, MAX(t.year*100+t.month) AS thang_cuoi FROM bi_org o
                           JOIN {t} t ON t.org_code = o.org_code GROUP BY o.org_code"""):  # noqa: S608
                if int(r["thang_cuoi"]) < last:
                    rows.append({"bang": t, "org_code": r["org_code"], "thang_cuoi": r["thang_cuoi"], "thang_moi_nhat": last})
    out.append(Check(L, f"Đơn vị có dữ liệu cũ (bán hàng < mốc − {STALE_DAYS} ngày; bảng tháng thiếu tháng cuối)",
                     WARN if rows else OK, f"{len(rows)} (bảng, đơn vị)" if rows else "Không có", rows[:MAX_ROWS]))
    return out


def run(period: Period) -> list[Check]:
    return catalog() + coverage(period) + anomalies(period)

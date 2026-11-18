"""Đối soát nhiều lớp trên hợp đồng bi_* – CHỈ ĐỌC, không sửa dữ liệu (Blueprint 9.1, 9.2, 17 tầng "Số liệu").

Lớp 1–2 (file gốc, ETL) không áp dụng: AI4BI đọc thẳng bi_*; file Excel "Dữ liệu của tôi" được kiểm khi tải lên.
  3. Bảng      – tổng chi tiết = tổng chung theo (tháng, đơn vị); năm / quý / tháng khớp ngày; trước CK − CK = thuần
  4. Ngữ nghĩa – chỉ tiêu qua semantic.run = SQL viết độc lập trên bảng, theo đơn vị                   (1 đồng)
  5. Dashboard – gọi thẳng build() của phân hệ (user toàn quyền) = SQL độc lập; toàn công ty, khu vực, đơn vị (1.000 đồng)
  Tài chính   – Tài sản = Nợ + Vốn; LCTT = thay đổi tiền; LNST P&L = LNST trên LCTT; cầu nối hoá đơn ↔ sổ cái (ℹ️)
Lệch thì chỉ rõ đơn vị / kỳ / chứng từ. Kết quả không chứa tên khách hàng.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core import db
from app.core.periods import Period, make
from app.core.scope import User

OK, INFO, WARN, FAIL, SKIP = "ok", "info", "warn", "fail", "skip"
ICON = {OK: "✅", INFO: "ℹ️", WARN: "⚠️", FAIL: "❌", SKIP: "➖"}
TOL_ROW, TOL_DASH, TOL_ROUND = 1.0, 1000.0, 5.0   # 1 đồng (lớp 3–4), 1.000 đồng (lớp 5), lệch làm tròn từng dòng
MAX_ROWS = 50                                      # số dòng lệch giữ lại cho mỗi kiểm tra

RECON_USER = User("recon", "Đối soát", "admin", ("*",), None, is_admin=True)


@dataclass
class Check:
    layer: str
    name: str
    status: str
    detail: str
    rows: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------ truy vấn
def q(sql: str, params: dict | None = None) -> list[dict]:
    return db.fetch(sql, params)


def has(table: str) -> bool:
    try:
        return bool(q(f"SELECT 1 AS x FROM {table} LIMIT 1"))  # noqa: S608 – tên bảng cố định
    except Exception:  # noqa: BLE001 – kho khách chưa có view này
        return False


def num(v) -> float:
    return float(v or 0)


def money(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def ym(year, month) -> str:
    return f"T{int(month)}/{int(year)}"


def default_period() -> Period:
    m = db.meta().get("last_closed_month") or ""
    if len(m) == 7:
        return make(int(m[:4]), int(m[5:]))
    r = q("SELECT MAX(year*100+month) AS v FROM bi_sales")[0]["v"]
    return make(int(r) // 100, int(r) % 100)


def orgs() -> list[dict]:
    return q("SELECT org_code, org_name, region_name FROM bi_org ORDER BY sort_order, org_code")


# ------------------------------------------------------------------ định nghĩa độc lập (không đọc metrics.yaml)
_PNL = "scenario = 'actual' AND line_code IN ({})"
_REV = "'DT_BAN_HANG','DT_NOI_BO','GIAM_TRU'"
_BSG = "line_code IN (SELECT line_code FROM bi_bs_lines WHERE grp = '{}')"
_CFG = "line_code IN (SELECT line_code FROM bi_cf_lines WHERE grp = '{}')"
INDEP = {  # chỉ tiêu -> (bảng, biểu thức, điều kiện, kind, có cột org_code)
    "net_revenue": ("bi_sales", "net_revenue", None, "flow", True),
    "gross_sales": ("bi_sales", "gross_amount", None, "flow", True),
    "discount": ("bi_sales", "discount_amount", None, "flow", True),
    "sales_cogs": ("bi_sales", "cogs", None, "flow", True),
    "sales_gross_profit": ("bi_sales", "net_revenue - cogs", None, "flow", True),
    "plan_revenue": ("bi_sales_plan", "plan_revenue", None, "flow", True),
    "pnl_revenue": ("bi_pnl", "amount", _PNL.format(_REV), "flow", True),
    "gross_profit": ("bi_pnl", "amount", _PNL.format(_REV + ",'GIA_VON'"), "flow", True),
    "ebitda": ("bi_pnl", "amount", _PNL.format(_REV + ",'GIA_VON','CP_BH','CP_QLDN'"), "flow", True),
    "npat": ("bi_pnl", "amount", "scenario = 'actual'", "flow", True),
    "total_assets": ("bi_bs", "amount", _BSG.format("A"), "snapshot", True),
    "total_liabilities": ("bi_bs", "amount", _BSG.format("L"), "snapshot", True),
    "equity": ("bi_bs", "amount", _BSG.format("E"), "snapshot", True),
    "cash": ("bi_bs", "amount", "line_code = 'TIEN'", "snapshot", True),
    "cfo": ("bi_cashflow", "amount", _CFG.format("CFO"), "flow", True),
    "cfi": ("bi_cashflow", "amount", _CFG.format("CFI"), "flow", True),
    "cff": ("bi_cashflow", "amount", _CFG.format("CFF"), "flow", True),
    "net_cash_flow": ("bi_cashflow", "amount", None, "flow", True),
    "inventory_value": ("bi_inventory", "value_end", None, "snapshot", False),
}


def _where(kind: str, cond: str | None, period: Period, codes: list[str] | None) -> tuple[str, dict]:
    w = ["year*100+month = :e" if kind == "snapshot" else "year*100+month BETWEEN :s AND :e"]
    p = {"s": period.start, "e": period.end}
    if cond:
        w.append(cond)
    if codes is not None:
        names = []
        for i, c in enumerate(codes):
            p[f"o{i}"] = c
            names.append(f":o{i}")
        w.append(f"org_code IN ({', '.join(names) or 'NULL'})")
    return " AND ".join(w), p


def expected(key: str, period: Period, codes: list[str] | None = None) -> float:
    table, expr, cond, kind, _ = INDEP[key]
    w, p = _where(kind, cond, period, codes)
    return num(q(f"SELECT SUM({expr}) AS v FROM {table} WHERE {w}", p)[0]["v"])  # noqa: S608


def expected_by_org(key: str, period: Period) -> dict[str, float]:
    table, expr, cond, kind, _ = INDEP[key]
    w, p = _where(kind, cond, period, None)
    return {r["org_code"]: num(r["v"]) for r in
            q(f"SELECT org_code, SUM({expr}) AS v FROM {table} WHERE {w} GROUP BY org_code", p)}  # noqa: S608


def _metrics_with_data() -> list[str]:
    tables = {t for t, *_ in INDEP.values()}
    ok = {t for t in tables if has(t)}
    return [k for k, v in INDEP.items() if v[0] in ok]


# ------------------------------------------------------------------ lớp 3: bảng
MONTHLY = ("bi_sales", "bi_sales_plan", "bi_pnl", "bi_cost_detail", "bi_bs", "bi_cashflow", "bi_inventory")


def layer_table() -> list[Check]:
    L, out = "3. Bảng", []
    if not has("bi_sales"):
        return [Check(L, "Doanh thu (bi_sales)", SKIP, "Chưa có dữ liệu bán hàng")]
    grand = q("SELECT COUNT(*) AS n, SUM(net_revenue) AS v FROM bi_sales")[0]
    parts = q("""SELECT year, month, org_code, SUM(net_revenue) AS v FROM bi_sales
                 WHERE year IS NOT NULL AND month IS NOT NULL AND org_code IN (SELECT org_code FROM bi_org)
                 GROUP BY year, month, org_code""")
    diff = num(grand["v"]) - sum(num(r["v"]) for r in parts)
    rows = []
    if abs(diff) > TOL_ROW:
        rows = q(f"""SELECT order_no, CAST(date AS VARCHAR(10)) AS date, year, month, org_code, net_revenue FROM bi_sales
                     WHERE year IS NULL OR month IS NULL OR org_code IS NULL
                        OR org_code NOT IN (SELECT org_code FROM bi_org) LIMIT {MAX_ROWS}""")
    out.append(Check(L, "Tổng chi tiết (tháng × đơn vị) = tổng chung", FAIL if abs(diff) > TOL_ROW else OK,
                     f"Tổng chung {money(num(grand['v']))} đ / {grand['n']} dòng; ngoài nhóm (tháng, đơn vị): {money(diff)} đ",
                     rows))
    bad = q(f"""SELECT order_no, CAST(date AS VARCHAR(10)) AS date, year, quarter, month, org_code FROM bi_sales
                WHERE SUBSTR(CAST(date AS VARCHAR(10)), 1, 7) <> {_ym_text()}
                   OR quarter IS NULL OR quarter <> (month + 2) / 3 LIMIT {MAX_ROWS}""")
    out.append(Check(L, "Năm / quý / tháng khớp ngày chứng từ", FAIL if bad else OK,
                     f"{len(bad)}{'+' if len(bad) == MAX_ROWS else ''} dòng lệch" if bad else "Khớp toàn bộ", bad))
    for t in MONTHLY[1:]:
        if has(t):
            b = q(f"SELECT year, quarter, month, COUNT(*) AS n FROM {t} "  # noqa: S608
                  f"WHERE quarter IS NULL OR month NOT BETWEEN 1 AND 12 OR quarter <> (month + 2) / 3 "
                  f"GROUP BY year, quarter, month LIMIT {MAX_ROWS}")
            if b:
                out.append(Check(L, f"Quý / tháng hợp lệ ({t})", FAIL, f"{len(b)} kỳ sai", b))
    s = q("""SELECT COUNT(*) AS n, MAX(ABS(gross_amount - discount_amount - net_revenue)) AS mx,
                    SUM(gross_amount - discount_amount - net_revenue) AS tot FROM bi_sales
             WHERE gross_amount IS NOT NULL AND ABS(gross_amount - COALESCE(discount_amount, 0) - net_revenue) > :t""",
          {"t": TOL_ROW})[0]
    n, mx = int(s["n"] or 0), num(s["mx"])
    rows = q(f"""SELECT order_no, org_code, year, month, gross_amount, discount_amount, net_revenue,
                        gross_amount - discount_amount - net_revenue AS lech FROM bi_sales
                 WHERE gross_amount IS NOT NULL AND ABS(gross_amount - COALESCE(discount_amount, 0) - net_revenue) > :t
                 ORDER BY ABS(gross_amount - discount_amount - net_revenue) DESC LIMIT {MAX_ROWS}""",
             {"t": TOL_ROW}) if n else []
    st = OK if not n else (INFO if mx <= TOL_ROUND else FAIL)
    det = "Khớp từng dòng" if not n else (f"{n} dòng lệch > 1 đ, lớn nhất {mx:,.1f} đ, tổng {money(num(s['tot']))} đ"
                                          + (" – lệch làm tròn (≤ 5 đ/dòng)" if st == INFO else ""))
    out.append(Check(L, "Trước chiết khấu − chiết khấu = doanh thu thuần (từng dòng)", st, det, rows))
    return out


def _ym_text() -> str:
    """'YYYY-MM' từ cột year, month – viết được cho SQLite và PostgreSQL."""
    return ("CAST(year AS VARCHAR(4)) || '-' || "
            "CASE WHEN month < 10 THEN '0' || CAST(month AS VARCHAR(2)) ELSE CAST(month AS VARCHAR(2)) END")


# ------------------------------------------------------------------ lớp 4: lớp ngữ nghĩa
def layer_semantic(period: Period) -> list[Check]:
    from app.core.semantic import Query, run
    L, out = "4. Ngữ nghĩa", []
    name_code = {o["org_name"]: o["org_code"] for o in orgs()}
    for p in (period, make(period.year, period.m_to, "ytd")):
        for key in _metrics_with_data():
            by_org = INDEP[key][4]
            res = run(Query([key], ["org"] if by_org else [], p), RECON_USER)
            if res.unavailable:
                out.append(Check(L, f"{key} – {p.label}", FAIL, "; ".join(res.notes) or "Không tính được"))
                continue
            rows = []
            if by_org:
                exp = expected_by_org(key, p)
                got = {name_code.get(r["org"], r["org"]): num(r[key]) for r in res.rows}
                for code in sorted(set(exp) | set(got)):
                    d = got.get(code, 0.0) - exp.get(code, 0.0)
                    if abs(d) > TOL_ROW:
                        rows.append({"org_code": code, "ky": p.label, "ngu_nghia": got.get(code, 0.0),
                                     "sql_doc_lap": exp.get(code, 0.0), "lech": d})
                total = sum(got.values())
            else:
                total = num(res.rows[0][key]) if res.rows else 0.0
                d = total - expected(key, p)
                if abs(d) > TOL_ROW:
                    rows.append({"ky": p.label, "ngu_nghia": total, "sql_doc_lap": total - d, "lech": d})
            out.append(Check(L, f"{key} – {p.label}", FAIL if rows else OK,
                             f"{len(rows)} đơn vị lệch > 1 đ" if rows else f"Khớp, tổng {money(total)} đ", rows))
    return out


# ------------------------------------------------------------------ lớp 5: dashboard
def scopes(level: str = "all") -> list[tuple[str, dict, list[str] | None]]:
    """(nhãn, bộ lọc dashboard, mã đơn vị cho SQL độc lập)."""
    os_ = orgs()
    out = [("Toàn công ty", {}, None)]
    if level in ("region", "all"):
        for rn in dict.fromkeys(o["region_name"] for o in os_ if o["region_name"]):
            out.append((f"Khu vực {rn}", {"region": [rn]}, [o["org_code"] for o in os_ if o["region_name"] == rn]))
    if level == "all":
        out += [(f"Đơn vị {o['org_code']}", {"org": [o["org_name"]]}, [o["org_code"]]) for o in os_]
    return out


def layer_dashboard(period: Period, level: str = "all") -> list[Check]:
    from app.modules import MENU, REGISTRY
    L, out = "5. Dashboard", []
    filterable = {k: f for _, items in MENU for k, _, _, f in items}
    avail = set(_metrics_with_data())
    for mod, m in REGISTRY.items():
        if mod == "overview" or not RECON_USER.can(mod):
            continue
        rows, n = [], 0
        for label, filters, codes in (scopes(level) if filterable.get(mod) else scopes("total")):
            try:
                page = m.build(period, RECON_USER, filters)
            except Exception as e:  # noqa: BLE001 – báo lỗi dựng trang như một lệch
                rows.append({"pham_vi": label, "chi_tieu": "(dựng trang)", "dashboard": None, "sql_doc_lap": None,
                             "lech": None, "loi": str(e)[:200]})
                continue
            for k in page["kpis"]:
                if k["key"] not in INDEP or k["key"] not in avail:
                    continue
                n += 1
                exp = expected(k["key"], period, codes)
                d = num(k["value"]) - exp
                if abs(d) > TOL_DASH:
                    rows.append({"pham_vi": label, "chi_tieu": k["key"], "dashboard": num(k["value"]),
                                 "sql_doc_lap": exp, "lech": d})
        out.append(Check(L, f"{mod} – {period.label}", FAIL if rows else (OK if n else SKIP),
                         f"{len(rows)} lệch > 1.000 đ trên {n} số" if rows else f"Khớp {n} số KPI", rows))
    return out


# ------------------------------------------------------------------ tài chính
def layer_finance(period: Period) -> list[Check]:
    L, out = "Tài chính", []
    if has("bi_bs") and has("bi_bs_lines"):
        rows = q(f"""SELECT b.year, b.month, b.org_code,
                       SUM(CASE WHEN l.grp = 'A' THEN b.amount ELSE 0 END) AS tai_san,
                       SUM(CASE WHEN l.grp IN ('L', 'E') THEN b.amount ELSE 0 END) AS nguon_von
                     FROM bi_bs b JOIN bi_bs_lines l ON l.line_code = b.line_code
                     WHERE b.year*100+b.month <= :e GROUP BY b.year, b.month, b.org_code
                     HAVING ABS(SUM(CASE WHEN l.grp = 'A' THEN b.amount ELSE 0 END)
                              - SUM(CASE WHEN l.grp IN ('L', 'E') THEN b.amount ELSE 0 END)) > :t
                     ORDER BY b.year, b.month, b.org_code LIMIT {MAX_ROWS}""", {"e": period.end, "t": TOL_DASH})
        for r in rows:
            r["lech"] = num(r["tai_san"]) - num(r["nguon_von"])
        out.append(Check(L, "Bảng cân đối: Tài sản = Nợ + Vốn (mọi tháng, mọi đơn vị)", FAIL if rows else OK,
                         f"{len(rows)} (tháng, đơn vị) không cân" if rows else "Cân toàn bộ", rows))
    if has("bi_cashflow") and has("bi_bs"):
        cash = {(r["org_code"], int(r["ym"])): num(r["v"]) for r in
                q("SELECT org_code, year*100+month AS ym, SUM(amount) AS v FROM bi_bs WHERE line_code = 'TIEN' "
                  "GROUP BY org_code, year*100+month")}
        rows, n = [], 0
        for r in q("SELECT org_code, year, month, SUM(amount) AS v FROM bi_cashflow WHERE year*100+month <= :e "
                   "GROUP BY org_code, year, month ORDER BY year, month, org_code", {"e": period.end}):
            cur = int(r["year"]) * 100 + int(r["month"])
            prv = cur - 1 if int(r["month"]) > 1 else (int(r["year"]) - 1) * 100 + 12
            if (r["org_code"], cur) not in cash or (r["org_code"], prv) not in cash:
                continue
            n += 1
            delta = cash[(r["org_code"], cur)] - cash[(r["org_code"], prv)]
            d = num(r["v"]) - delta
            if abs(d) > TOL_DASH and len(rows) < MAX_ROWS:
                rows.append({"org_code": r["org_code"], "ky": ym(r["year"], r["month"]), "lctt": num(r["v"]),
                             "thay_doi_tien": delta, "lech": d})
        out.append(Check(L, "Lưu chuyển tiền = thay đổi tiền trên bảng cân đối", FAIL if rows else (OK if n else SKIP),
                         f"{len(rows)} (tháng, đơn vị) lệch" if rows else f"Khớp {n} (tháng, đơn vị)", rows))
    if has("bi_cashflow") and has("bi_pnl"):
        rows = q(f"""SELECT p.org_code, p.year, p.month, p.v AS lnst_pnl, c.v AS lnst_lctt FROM
                       (SELECT org_code, year, month, SUM(amount) AS v FROM bi_pnl WHERE scenario = 'actual'
                        GROUP BY org_code, year, month) p
                     JOIN (SELECT org_code, year, month, SUM(amount) AS v FROM bi_cashflow WHERE line_code = 'LNST'
                           GROUP BY org_code, year, month) c
                       ON c.org_code = p.org_code AND c.year = p.year AND c.month = p.month
                     WHERE p.year*100+p.month <= :e AND ABS(p.v - c.v) > :t
                     ORDER BY p.year, p.month, p.org_code LIMIT {MAX_ROWS}""", {"e": period.end, "t": TOL_DASH})
        for r in rows:
            r["lech"] = num(r["lnst_pnl"]) - num(r["lnst_lctt"])
        out.append(Check(L, "LNST trên P&L = LNST đầu lưu chuyển tiền", FAIL if rows else OK,
                         f"{len(rows)} (tháng, đơn vị) lệch" if rows else "Khớp", rows))
    if has("bi_pnl") and has("bi_sales"):
        rows = []
        for p in (period, make(period.year, period.m_to, "ytd")):
            gl, inv = expected_by_org("pnl_revenue", p), expected_by_org("net_revenue", p)
            for code in sorted(set(gl) | set(inv)):
                rows.append({"org_code": code, "ky": p.label, "so_cai": gl.get(code, 0.0),
                             "hoa_don": inv.get(code, 0.0), "chenh": gl.get(code, 0.0) - inv.get(code, 0.0)})
        tot = sum(r["chenh"] for r in rows if r["ky"] == period.label)
        out.append(Check(L, "Cầu nối doanh thu hoá đơn ↔ doanh thu sổ cái", INFO,
                         f"{period.label}: sổ cái − hoá đơn = {money(tot)} đ (thông tin, không phải lỗi)", rows))
    return out


# ------------------------------------------------------------------ chạy tất cả
def run_all(period: Period | None = None, level: str = "all") -> tuple[Period, list[Check]]:
    from app.core import dq
    period = period or default_period()
    checks = layer_table() + layer_semantic(period) + layer_dashboard(period, level) + layer_finance(period)
    return period, checks + dq.run(period)


def worst(checks: list[Check]) -> str:
    order = [FAIL, WARN, INFO, OK, SKIP]
    return min((c.status for c in checks), key=order.index, default=OK)

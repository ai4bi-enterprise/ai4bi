"""Dựng kho dữ liệu demo theo HỢP ĐỒNG DỮ LIỆU (bi_*) từ bộ AdventureWorks DW công khai.

Nguồn: Microsoft SQL Server Samples – AdventureWorks Data Warehouse (giấy phép MIT)
https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works

Nguyên tắc (theo Blueprint P6 "ETL nguyên bản, view quyết định"):
  * Giữ số liệu gốc 1:1, chỉ đổi tên cột/nhãn sang chuẩn hợp đồng, không tự bịa số.
  * Các phép biến đổi DUY NHẤT, đều được ghi lại trong bảng bi_meta:
      - Dịch thời gian +13 năm (dữ liệu gốc 01/2011–09/2013 -> 01/2024–09/2026).
      - Sổ cái (FactFinance) ghi ngày 1–3 của tháng sau được trả về đúng tháng kế toán.
      - Đơn vị địa phương (CAD/EUR/AUD) quy về USD theo tỷ giá trung bình tháng của chính nguồn.
      - USD quy sang VND theo tỷ giá cố định minh hoạ (USD_VND).
      - Kế hoạch doanh thu = chỉ tiêu quý của nhân viên kinh doanh (FactSalesQuota), chia đều 3 tháng.
      - Báo cáo lưu chuyển tiền tệ lập theo phương pháp gián tiếp từ Bảng cân đối + P&L.
  * Khách lẻ online là cá nhân -> gộp về một mã, không giữ tên (Blueprint P5: tối thiểu dữ liệu cá nhân).

Chạy:  python tools/build_demo_db.py --raw data/raw --out data/aimoingay_demo.db
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import sqlite3

SHIFT_YEARS = 13
USD_VND = 25_000
START = (2011, 1)   # tháng gốc đầu tiên đưa vào kho (sau dịch: 01/2024)
END = (2013, 9)     # tháng gốc cuối cùng (sau dịch: 09/2026)
OPENING = (2010, 12)  # số dư đầu kỳ cho BS / lưu chuyển tiền

# ---------------------------------------------------------------- cây tổ chức
REGIONS = {"NA": ("Bắc Mỹ", 1), "EU": ("Châu Âu", 2), "PAC": ("Châu Á – Thái Bình Dương", 3)}
ORGS = [  # org_code, tên, quốc gia, khu vực, tiền tệ gốc, territory key, finance org key
    ("US-NW", "Hoa Kỳ – Tây Bắc", "Hoa Kỳ", "NA", "USD", 1, 4),
    ("US-NE", "Hoa Kỳ – Đông Bắc", "Hoa Kỳ", "NA", "USD", 2, 3),
    ("US-C", "Hoa Kỳ – Miền Trung", "Hoa Kỳ", "NA", "USD", 3, 5),
    ("US-SW", "Hoa Kỳ – Tây Nam", "Hoa Kỳ", "NA", "USD", 4, 7),
    ("US-SE", "Hoa Kỳ – Đông Nam", "Hoa Kỳ", "NA", "USD", 5, 6),
    ("CA", "Canada", "Canada", "NA", "CAD", 6, 8),
    ("FR", "Pháp", "Pháp", "EU", "EUR", 7, 11),
    ("DE", "Đức", "Đức", "EU", "EUR", 8, 12),
    ("UK", "Anh", "Anh", "EU", "GBP", 10, None),
    ("AU", "Úc", "Úc", "PAC", "AUD", 9, 13),
]
TERR2ORG = {o[5]: o[0] for o in ORGS}
FIN2ORG = {o[6]: o[0] for o in ORGS if o[6]}
CUR_KEY = {"USD": 100, "CAD": 19, "EUR": 36, "AUD": 6, "GBP": 98}

CATEGORY_VI = {"Bikes": "Xe đạp", "Components": "Linh kiện", "Clothing": "Quần áo", "Accessories": "Phụ kiện"}
BUSINESS_TYPE_VI = {"Value Added Reseller": "Đại lý giá trị gia tăng",
                    "Specialty Bike Shop": "Cửa hàng chuyên doanh", "Warehouse": "Nhà phân phối kho"}

# ---------------------------------------------------------------- P&L quản trị
# dòng lá: line_code -> (tên, thứ tự); tổng do lớp ngữ nghĩa (semantic/finance.yaml) tính
PNL_LEAF = {
    "DT_BAN_HANG": "Doanh thu bán hàng", "DT_NOI_BO": "Doanh thu nội bộ", "GIAM_TRU": "Các khoản giảm trừ doanh thu",
    "GIA_VON": "Giá vốn hàng bán", "CP_BH": "Chi phí bán hàng", "CP_QLDN": "Chi phí quản lý doanh nghiệp",
    "KHAU_HAO": "Khấu hao & phân bổ", "DT_TC": "Doanh thu tài chính", "CP_LAI_VAY": "Chi phí lãi vay",
    "TN_KHAC": "Thu nhập / chi phí khác", "THUE_TNDN": "Thuế TNDN",
}
ACC_PNL = {"4200": "DT_BAN_HANG", "4500": "DT_NOI_BO", "4130": "GIAM_TRU", "4140": "GIAM_TRU",
           "5020": "GIA_VON", "5050": "GIA_VON", "8000": "DT_TC", "8010": "CP_LAI_VAY",
           "8020": "TN_KHAC", "8030": "TN_KHAC", "8040": "TN_KHAC", "8500": "THUE_TNDN"}
DEPRECIATION = {"6810", "6820", "6830", "6840", "6850", "6860"}
SALES_DEPT = "7"  # Sales and Marketing -> chi phí bán hàng; phòng ban khác -> QLDN
SELLING_ACCOUNTS = {"6100", "6300", "6310"}  # hoa hồng, hội nghị, ấn phẩm marketing luôn là chi phí bán hàng

ACCOUNT_VI = {
    "4200": "Doanh thu bán hàng", "4500": "Doanh thu nội bộ", "4130": "Hàng bán bị trả lại", "4140": "Chiết khấu thương mại",
    "5020": "Giá vốn tiêu chuẩn", "5050": "Chênh lệch giá vốn", "6000": "Lương", "6020": "Thuế trên lương",
    "6040": "Phúc lợi nhân viên", "6100": "Hoa hồng bán hàng", "6200": "Công tác – đi lại", "6210": "Công tác – lưu trú",
    "6220": "Ăn uống", "6230": "Tiếp khách", "6240": "Công tác khác", "6300": "Hội nghị, sự kiện",
    "6310": "Ấn phẩm marketing", "6400": "Văn phòng phẩm", "6500": "Dịch vụ chuyên môn", "6610": "Điện thoại",
    "6620": "Điện nước", "6700": "Chi phí khác", "6810": "KH nhà xưởng thuê", "6820": "KH phương tiện",
    "6830": "KH máy móc", "6840": "KH nội thất", "6850": "KH tài sản khác", "6860": "Phân bổ lợi thế thương mại",
    "6920": "Thuê mặt bằng", "8000": "Lãi tiền gửi", "8010": "Lãi vay", "8020": "Lãi/lỗ thanh lý tài sản",
    "8030": "Thu nhập khác", "8040": "Chênh lệch tỷ giá", "8500": "Thuế TNDN",
}

# ---------------------------------------------------------------- Bảng cân đối
BS_LEAF = [  # line_code, tên, nhóm (A/L/E), tài khoản nguồn
    ("TIEN", "Tiền và tương đương tiền", "A", ["1110"]),
    ("PT_KH", "Phải thu khách hàng", "A", ["1130"]),
    ("PT_KHAC", "Phải thu khác & dự phòng", "A", ["1140", "1150", "1185"]),
    ("HTK", "Hàng tồn kho", "A", ["1162", "1164", "1166"]),
    ("TSNH_KHAC", "Tài sản ngắn hạn khác", "A", ["1170", "1180"]),
    ("TSCD", "Tài sản cố định", "A", ["1210", "1220", "1230", "1240", "1250", "1260"]),
    ("TSDH_KHAC", "Tài sản dài hạn khác", "A", ["1300"]),
    ("VAY_NH", "Vay ngắn hạn", "L", ["2210", "2220"]),
    ("PT_NB", "Phải trả người bán", "L", ["2230"]),
    ("CP_PHAI_TRA", "Chi phí phải trả", "L", ["2310", "2320", "2330"]),
    ("NPT_NH_KHAC", "Nợ ngắn hạn khác", "L", ["2340", "2350"]),
    ("VAY_DH", "Vay dài hạn", "L", ["2410"]),
    ("NPT_DH_KHAC", "Nợ dài hạn khác", "L", ["2420", "2430", "2440"]),
    ("VON_GOP", "Vốn góp chủ sở hữu", "E", ["3010", "3020"]),
    ("LNST_CPP", "Lợi nhuận sau thuế chưa phân phối", "E", ["3540", "3550"]),
]
ACC_BS = {acc: line for line, _, _, accs in BS_LEAF for acc in accs}

CF_LINES = [  # line_code, tên, nhóm, thứ tự
    ("LNST", "Lợi nhuận sau thuế", "CFO", 1), ("KHAU_HAO", "Khấu hao & phân bổ", "CFO", 2),
    ("D_PT_KH", "(Tăng)/giảm phải thu khách hàng", "CFO", 3), ("D_HTK", "(Tăng)/giảm hàng tồn kho", "CFO", 4),
    ("D_TS_KHAC", "(Tăng)/giảm tài sản lưu động khác", "CFO", 5), ("D_PT_NB", "Tăng/(giảm) phải trả người bán", "CFO", 6),
    ("D_NO_KHAC", "Tăng/(giảm) nợ phải trả khác", "CFO", 7),
    ("CAPEX", "Đầu tư tài sản cố định & dài hạn", "CFI", 8),
    ("D_VAY", "Vay / (trả) nợ vay", "CFF", 9), ("D_VON", "Tăng/(giảm) vốn góp", "CFF", 10),
    ("PP_LN", "Phân phối lợi nhuận & điều chỉnh vốn khác", "CFF", 11),
]


def rows(raw: str, name: str):
    with open(os.path.join(raw, name), encoding="utf-8-sig", errors="replace") as h:
        for line in h:
            line = line.rstrip("\r\n")
            if line:
                yield line.split("|")


def shift(d: dt.date) -> dt.date:
    try:
        return d.replace(year=d.year + SHIFT_YEARS)
    except ValueError:  # 29/02
        return d.replace(year=d.year + SHIFT_YEARS, day=28)


def dk(s: str) -> dt.date:
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def in_window(y: int, m: int, start=START) -> bool:
    return start <= (y, m) <= END


def fin_month(d: dt.date) -> tuple[int, int]:
    """Sổ cái AdventureWorks ghi số tháng vào ngày 1–3 tháng sau ở một số kỳ -> trả về tháng kế toán."""
    if d.day <= 3:
        d -= dt.timedelta(days=5)
    return d.year, d.month


def build(raw: str, out: str) -> None:
    if os.path.exists(out):
        os.remove(out)
    con = sqlite3.connect(out)
    con.executescript(open(os.path.join(os.path.dirname(__file__), "contract_schema.sql"), encoding="utf-8").read())

    # ---- tỷ giá trung bình tháng (USD trên 1 đơn vị tiền địa phương)
    fx = collections.defaultdict(list)
    for r in rows(raw, "FactCurrencyRate.csv"):
        d = dk(r[1])
        fx[(int(r[0]), d.year, d.month)].append(float(r[2]))
    fxm = {k: sum(v) / len(v) for k, v in fx.items()}

    def to_vnd(amount: float, cur: str, y: int, m: int) -> float:
        rate = 1.0 if cur == "USD" else fxm[(CUR_KEY[cur], y, m)]
        return amount * rate * USD_VND

    # ---- tổ chức
    con.executemany("INSERT INTO bi_org VALUES (?,?,?,?,?,?,?)", [
        (code, name, country, reg, REGIONS[reg][0], cur, i + 1) for i, (code, name, country, reg, cur, _, _) in enumerate(ORGS)])

    # ---- sản phẩm
    cat = {r[0]: CATEGORY_VI.get(r[2], r[2]) for r in rows(raw, "DimProductCategory.csv")}
    sub = {r[0]: (r[2], cat[r[5]]) for r in rows(raw, "DimProductSubcategory.csv")}
    prod = {}
    for r in rows(raw, "DimProduct.csv"):
        if len(r) < 20 or not r[0].isdigit():
            continue
        subname, catname = sub.get(r[2], ("Khác", "Khác"))
        prod[r[0]] = dict(code=r[1], name=r[5], sub=subname, cat=catname,
                          safety=int(r[11] or 0), reorder=int(r[12] or 0))

    # ---- khách hàng đại lý
    resellers = {r[0]: (r[2], r[5], BUSINESS_TYPE_VI.get(r[4], r[4])) for r in rows(raw, "DimReseller.csv")}

    # ---- doanh thu (chi tiết dòng hoá đơn)
    sales = []
    for r in rows(raw, "FactResellerSales.csv"):
        d = dk(r[1])
        if not in_window(d.year, d.month):
            continue
        sd = shift(d)
        code, name, ctype = resellers[r[4]]
        p = prod[r[0]]
        sales.append((sd.isoformat(), sd.year, (sd.month - 1) // 3 + 1, sd.month, TERR2ORG[int(r[8])], "Đại lý",
                      code, name, ctype, p["code"], p["name"], p["cat"], p["sub"], r[9],
                      int(r[12]), float(r[14]) * USD_VND, float(r[16]) * USD_VND, float(r[19]) * USD_VND,
                      float(r[18]) * USD_VND))
    for r in rows(raw, "FactInternetSales.csv"):
        d = dk(r[1])
        if not in_window(d.year, d.month):
            continue
        sd = shift(d)
        p = prod[r[0]]
        sales.append((sd.isoformat(), sd.year, (sd.month - 1) // 3 + 1, sd.month, TERR2ORG[int(r[7])], "Online",
                      "ONLINE", "Khách lẻ online", "Khách lẻ", p["code"], p["name"], p["cat"], p["sub"], r[8],
                      int(r[11]), float(r[13]) * USD_VND, float(r[15]) * USD_VND, float(r[18]) * USD_VND,
                      float(r[17]) * USD_VND))
    con.executemany(f"INSERT INTO bi_sales VALUES ({','.join('?' * 19)})", sales)

    # ---- kế hoạch doanh thu: chỉ tiêu quý của nhân viên kinh doanh (kênh Đại lý) chia đều 3 tháng
    emp_terr = {}
    for r in rows(raw, "DimEmployee.csv"):
        if len(r) > 4 and r[0].isdigit() and r[4].isdigit():
            emp_terr[r[0]] = int(r[4])
    plan = collections.defaultdict(float)
    for r in rows(raw, "FactSalesQuota.csv"):
        d = dk(r[2])
        if d.day <= 3:
            d -= dt.timedelta(days=5)
        q_start = (d.month - 1) // 3 * 3 + 1
        terr = emp_terr.get(r[1])
        if terr not in TERR2ORG:
            continue
        for m in range(q_start, q_start + 3):
            if in_window(d.year, m):
                plan[(d.year + SHIFT_YEARS, m, TERR2ORG[terr])] += float(r[5]) / 3 * USD_VND
    con.executemany("INSERT INTO bi_sales_plan VALUES (?,?,?,?,?,?)",
                    [(y, (m - 1) // 3 + 1, m, org, "Đại lý", v) for (y, m, org), v in plan.items()])

    # ---- sổ cái -> P&L, chi phí theo tài khoản, bảng cân đối
    acc = {r[0]: r for r in rows(raw, "DimAccount.csv")}
    org_cur = {o[0]: o[4] for o in ORGS}
    pnl = collections.defaultdict(float)
    opex = collections.defaultdict(float)
    bs = collections.defaultdict(float)
    for r in rows(raw, "FactFinance.csv"):
        y, m = fin_month(dk(r[1]))
        if not in_window(y, m, OPENING):
            continue
        org = FIN2ORG.get(int(r[2]))
        if org is None:
            continue
        a = acc[r[5]]
        code, atype = a[2], a[5]
        scen = {"1": "actual", "2": "plan"}.get(r[4])
        if scen is None:
            continue
        v = to_vnd(float(r[6]), org_cur[org], y, m)
        ys = y + SHIFT_YEARS
        if code in ACC_BS:
            if scen == "actual":
                bs[(ys, m, org, ACC_BS[code])] += v
            continue
        if (y, m) < START:
            continue
        sign = 1 if atype == "Revenue" else -1
        if code in ACC_PNL:
            line = ACC_PNL[code]
        elif code in DEPRECIATION:
            line = "KHAU_HAO"
        elif code.startswith("6"):
            line = "CP_BH" if r[3] == SALES_DEPT or code in SELLING_ACCOUNTS else "CP_QLDN"
        else:
            continue
        pnl[(ys, m, org, scen, line)] += sign * v
        if code.startswith(("5", "6")) and scen == "actual":
            opex[(ys, m, org, line, code, ACCOUNT_VI.get(code, a[4]))] += -sign * v
    con.executemany("INSERT INTO bi_pnl VALUES (?,?,?,?,?,?,?)",
                    [(y, (m - 1) // 3 + 1, m, org, scen, line, v) for (y, m, org, scen, line), v in pnl.items()])
    con.executemany("INSERT INTO bi_cost_detail VALUES (?,?,?,?,?,?,?,?)",
                    [(y, (m - 1) // 3 + 1, m, org, line, code, name, v)
                     for (y, m, org, line, code, name), v in opex.items()])
    con.executemany("INSERT INTO bi_bs VALUES (?,?,?,?,?,?)",
                    [(y, (m - 1) // 3 + 1, m, org, line, v) for (y, m, org, line), v in bs.items()])
    con.executemany("INSERT INTO bi_bs_lines VALUES (?,?,?,?)",
                    [(code, name, grp, i + 1) for i, (code, name, grp, _) in enumerate(BS_LEAF)])
    con.executemany("INSERT INTO bi_pnl_lines VALUES (?,?,?)",
                    [(code, name, i + 1) for i, (code, name) in enumerate(PNL_LEAF.items())])

    # ---- kiểm cân đối: Tài sản = Nợ + Vốn từng đơn vị, từng tháng (Blueprint P7: mọi con số đối soát được)
    grp = {code: g for code, _, g, _ in BS_LEAF}
    chk = collections.defaultdict(float)
    for (y, m, org, line), v in bs.items():
        chk[(y, m, org)] += v if grp[line] == "A" else -v
    worst = max(abs(v) for v in chk.values())
    assert worst < 1_000, f"Bảng cân đối lệch {worst:,.0f} đ"

    # ---- lưu chuyển tiền tệ gián tiếp
    months = sorted({(y, m) for (y, m, _, _) in bs})
    cf = []
    for org in {o for (_, _, o, _) in bs}:
        for prev, cur in zip(months, months[1:]):
            g = lambda line, ym=cur: bs.get((*ym, org, line), 0.0)
            p = lambda line: bs.get((*prev, org, line), 0.0)
            d = lambda *lines: sum(g(l) - p(l) for l in lines)
            y, m = cur
            ni = sum(pnl.get((y, m, org, "actual", l), 0.0) for l in PNL_LEAF)
            dep = -pnl.get((y, m, org, "actual", "KHAU_HAO"), 0.0)
            vals = {
                "LNST": ni, "KHAU_HAO": dep, "D_PT_KH": -d("PT_KH"), "D_HTK": -d("HTK"),
                "D_TS_KHAC": -d("PT_KHAC", "TSNH_KHAC"), "D_PT_NB": d("PT_NB"),
                "D_NO_KHAC": d("CP_PHAI_TRA", "NPT_NH_KHAC", "NPT_DH_KHAC"),
                "CAPEX": -(d("TSCD", "TSDH_KHAC") + dep),
                "D_VAY": d("VAY_NH", "VAY_DH"), "D_VON": d("VON_GOP"), "PP_LN": d("LNST_CPP") - ni,
            }
            assert abs(sum(vals.values()) - d("TIEN")) < 1_000, (org, cur)
            cf += [(y, (m - 1) // 3 + 1, m, org, k, v) for k, v in vals.items()]
    con.executemany("INSERT INTO bi_cashflow VALUES (?,?,?,?,?,?)", cf)
    con.executemany("INSERT INTO bi_cf_lines VALUES (?,?,?,?)", CF_LINES)

    # ---- tồn kho: chụp cuối tháng từ biến động ngày
    inv = {}
    flow = collections.defaultdict(lambda: [0, 0])
    for r in rows(raw, "FactProductInventory.csv"):
        d = dk(r[1])
        if not in_window(d.year, d.month):
            continue
        k = (d.year + SHIFT_YEARS, d.month, r[0])
        flow[k][0] += int(r[4])
        flow[k][1] += int(r[5])
        if k not in inv or d >= inv[k][0]:
            inv[k] = (d, float(r[3]), int(r[6]))
    agg: dict = {}  # một mã hàng có nhiều phiên bản giá (ProductKey) -> gộp về 1 dòng / mã / tháng
    for (y, m, pk), (_, cost, bal) in inv.items():
        p = prod[pk]
        a = agg.setdefault((y, m, p["code"]), {"p": p, "in": 0, "out": 0, "units": 0, "value": 0.0})
        a["in"] += flow[(y, m, pk)][0]
        a["out"] += flow[(y, m, pk)][1]
        a["units"] += bal
        a["value"] += bal * cost * USD_VND
        a["cost"] = cost * USD_VND
    inv_rows = []
    for (y, m, code), a in agg.items():
        p = a["p"]
        unit_cost = a["value"] / a["units"] if a["units"] else a["cost"]
        inv_rows.append((y, (m - 1) // 3 + 1, m, "KHO-TT", code, p["name"], p["cat"], p["sub"],
                         a["in"], a["out"], a["units"], unit_cost, a["value"], p["safety"], p["reorder"]))
    con.executemany(f"INSERT INTO bi_inventory VALUES ({','.join('?' * 15)})", inv_rows)

    # ---- siêu dữ liệu
    meta = {
        "company_name": "Công ty AI Mỗi Ngày", "currency": "VND", "as_of_date": "2026-09-30",
        "last_closed_month": "2026-09", "source": "Microsoft AdventureWorks DW (MIT) – github.com/microsoft/sql-server-samples",
        "transform_note": (f"Dữ liệu mẫu 01/2011–09/2013 dịch +{SHIFT_YEARS} năm; tiền địa phương quy USD theo tỷ giá tháng "
                           f"của nguồn; 1 USD = {USD_VND:,} VND (minh hoạ); kế hoạch doanh thu = chỉ tiêu quý kênh Đại lý "
                           "chia đều 3 tháng; kế hoạch P&L chỉ có cho năm 2024; lưu chuyển tiền tệ lập gián tiếp từ BS + P&L."),
    }
    con.executemany("INSERT INTO bi_meta VALUES (?,?)", meta.items())
    con.commit()
    for t in ("bi_org", "bi_sales", "bi_sales_plan", "bi_pnl", "bi_cost_detail", "bi_bs", "bi_cashflow", "bi_inventory"):
        print(f"{t:16s}{con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>10,}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/aimoingay_demo.db")
    a = ap.parse_args()
    build(a.raw, a.out)

"""
Sinh bộ dữ liệu mẫu cho AI4BI Chatbot Community Edition.

Công ty giả lập: "Công ty CP Phân phối Hàng tiêu dùng Sao Việt" (dữ liệu hư cấu 100%).
Phạm vi: 01/01/2025 – 31/12/2025 (4 quý), 3 chi nhánh, 20 sản phẩm, 66 khách hàng.

"Câu chuyện quản trị" được cài cố ý vào Quý 3/2025:
  • Bề nổi : Doanh thu thuần Q3 tăng +18% so với Q2 (vượt KPI).
  • Bề chìm: Lợi nhuận thuần và Dòng tiền thực thu Q3 giảm -12% so với Q2 vì
      - Chi nhánh Miền Nam chạy chiết khấu 22% cho Nhóm A (trần chính sách 10%),
        dồn hàng chủ yếu cho đại lý.
      - Công nợ quá hạn >90 ngày của nhóm Đại lý cấp 2 tăng vọt.

Chạy:  python tools/generate_sample_data.py
Kết quả: sample_data/enterprise_sample.db (ghi đè). Seed cố định => tái lập 100%.
"""
from __future__ import annotations

import calendar
import os
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

# Console Windows mặc định dùng cp1252 -> in tiếng Việt sẽ lỗi UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "sample_data" / "enterprise_sample.db"
SEED = 2025
YEAR = 2025

# ---- Mục tiêu câu chuyện (Q3 so với Q2) -------------------------------------
TARGET_REV_GROWTH = 0.18
TARGET_PROFIT_GROWTH = -0.12
TARGET_CASH_GROWTH = -0.12
SOUTH_PROMO_DISCOUNT = 0.22

# ---- Danh mục ---------------------------------------------------------------
BRANCHES = [
    # MaCN, TenCN, Vung, ThanhPho, quy mô gộp/tháng (VND)
    ("CN01", "Miền Bắc", "Bắc", "Hà Nội", 14.0e9),
    ("CN02", "Miền Trung", "Trung", "Đà Nẵng", 8.0e9),
    ("CN03", "Miền Nam", "Nam", "TP. Hồ Chí Minh", 18.0e9),
]
PROVINCES = {
    "CN01": ["Hà Nội", "Hải Phòng", "Bắc Ninh", "Nam Định", "Thái Nguyên"],
    "CN02": ["Đà Nẵng", "Huế", "Quảng Nam", "Bình Định", "Khánh Hòa"],
    "CN03": ["TP. Hồ Chí Minh", "Bình Dương", "Đồng Nai", "Cần Thơ", "Long An"],
}
GROUPS = {
    # MaNhom: (TenNhom, trần chiết khấu, tỷ lệ giá vốn/giá niêm yết)
    "A": ("Nhóm A - Đồ uống", 0.10, 0.74),
    "B": ("Nhóm B - Thực phẩm khô", 0.08, 0.76),
    "C": ("Nhóm C - Hóa mỹ phẩm", 0.12, 0.70),
    "D": ("Nhóm D - Gia vị & Dầu ăn", 0.08, 0.78),
}
PRODUCTS = [
    ("A", "Nước suối 500ml (thùng 24)", 85_000),
    ("A", "Trà xanh 455ml (thùng 24)", 190_000),
    ("A", "Nước tăng lực 250ml (thùng 24)", 240_000),
    ("A", "Nước ngọt có ga 330ml (thùng 24)", 210_000),
    ("A", "Sữa đậu nành 200ml (thùng 36)", 230_000),
    ("B", "Mì gói (thùng 30)", 105_000),
    ("B", "Bánh quy hộp (thùng 12)", 360_000),
    ("B", "Phở ăn liền (thùng 30)", 220_000),
    ("B", "Cháo gói (thùng 50)", 250_000),
    ("B", "Bún khô (thùng 20)", 300_000),
    ("C", "Bột giặt 3kg (thùng 4)", 520_000),
    ("C", "Nước rửa chén 750ml (thùng 12)", 300_000),
    ("C", "Dầu gội 650ml (thùng 12)", 1_080_000),
    ("C", "Kem đánh răng (thùng 36)", 900_000),
    ("C", "Nước lau sàn 1L (thùng 12)", 360_000),
    ("D", "Dầu ăn 1L (thùng 12)", 560_000),
    ("D", "Nước mắm 500ml (thùng 12)", 420_000),
    ("D", "Hạt nêm 400g (thùng 24)", 960_000),
    ("D", "Nước tương 500ml (thùng 24)", 380_000),
    ("D", "Bột ngọt 1kg (thùng 10)", 650_000),
]
CUSTOMER_TYPES = {
    # Loai: (số KH/chi nhánh, tỷ trọng doanh thu, kỳ hạn nợ (ngày), số HĐ/tháng, CK thường (min,max))
    "Đại lý cấp 1": (4, 0.30, 30, 6, (0.05, 0.07)),
    "Đại lý cấp 2": (9, 0.35, 45, 4, (0.03, 0.05)),
    "Siêu thị (MT)": (3, 0.20, 60, 4, (0.06, 0.075)),
    "Bán lẻ": (6, 0.15, 7, 8, (0.00, 0.02)),
}
NAME_PARTS = ["Minh Phát", "Thành Công", "Hưng Thịnh", "Phú Quý", "An Khang", "Tân Tiến",
              "Hoàng Long", "Kim Ngân", "Đại Phát", "Thuận Thành", "Vạn Lộc", "Hòa Bình",
              "Phước Lợi", "Nhật Quang", "Song Hỷ", "Bảo Châu", "Trường Thịnh", "Đức Tài",
              "Hồng Phúc", "Gia Hưng", "Quang Vinh", "Tấn Lộc", "Thiên Ân", "Mỹ Lan",
              "Hải Đăng", "Sơn Hà", "Ngọc Mai", "Toàn Phát", "Việt Tín", "Lộc Phát",
              "Phương Đông", "Tài Lộc", "Kim Long", "Hưng Long", "Đông Á", "Nam Việt",
              "Thịnh Vượng", "Phúc Khang", "Hoàng Gia", "Minh Châu", "An Phú", "Tín Nghĩa",
              "Hợp Thành", "Đại Dương", "Kim Thành", "Bình Minh", "Hưng Phát", "Trung Kiên",
              "Phú Thịnh", "Quốc Bảo", "Hải Yến", "Thanh Bình", "Ánh Dương", "Đức Thịnh",
              "Vĩnh Phát", "Mai Anh", "Phát Đạt", "Hòa Phát", "Tân Phú", "Lâm Viên",
              "Thái Sơn", "Xuân Mai", "Kim Phượng", "Hồng Hà", "Long Hải", "Cát Tường"]
TYPE_PREFIX = {"Đại lý cấp 1": "NPP", "Đại lý cấp 2": "Đại lý", "Siêu thị (MT)": "Siêu thị",
               "Bán lẻ": "Tạp hóa"}
SEASON = {1: 1.18, 2: 0.82, 3: 1.00, 4: 0.98, 5: 1.00, 6: 1.02,
          7: 1.03, 8: 1.03, 9: 1.03, 10: 1.05, 11: 1.08, 12: 1.15}
OPEX_RATES = {  # % doanh thu thuần
    "Lương & thưởng bán hàng": 0.050,
    "Vận chuyển & giao hàng": 0.030,
    "Khuyến mại & trưng bày": 0.020,
    "Thuê kho bãi": 0.015,
    "Chi phí quản lý chung": 0.030,
}


def quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def month_days(y: int, m: int) -> list[date]:
    n = calendar.monthrange(y, m)[1]
    return [date(y, m, d) for d in range(1, n + 1) if date(y, m, d).weekday() != 6]


def build() -> None:
    rnd = random.Random(SEED)
    log = lambda s: print(f"[generate] {s}", flush=True)  # noqa: E731
    log("Bắt đầu sinh dữ liệu mẫu (seed=%d)" % SEED)

    # ---------------- Danh mục sản phẩm & khách hàng ----------------
    products = []
    for i, (g, name, price) in enumerate(PRODUCTS, start=1):
        cost = round(price * GROUPS[g][2] * rnd.uniform(0.98, 1.02), -2)
        products.append({"MaSP": f"SP{i:03d}", "TenSP": name, "MaNhom": g, "TenNhom": GROUPS[g][0],
                         "DonViTinh": "Thùng", "GiaNiemYet": price, "GiaVon": cost,
                         "TranChietKhau": GROUPS[g][1]})
    prod_by_group = {g: [p for p in products if p["MaNhom"] == g] for g in GROUPS}

    customers = []
    names = NAME_PARTS[:]
    rnd.shuffle(names)
    ni = 0
    k = 1
    for cn, _, _, _, _ in BRANCHES:
        for ctype, (n, _, terms, _, _) in CUSTOMER_TYPES.items():
            for _ in range(n):
                nm = names[ni % len(names)]
                suffix = "" if ni < len(names) else f" {ni // len(names) + 1}"
                ni += 1
                prov = rnd.choice(PROVINCES[cn])
                customers.append({"MaKH": f"KH{k:03d}", "TenKH": f"{TYPE_PREFIX[ctype]} {nm}{suffix}",
                                  "LoaiKH": ctype, "MaCN": cn, "TinhThanh": prov,
                                  "SoNgayNo": terms, "weight": rnd.uniform(0.6, 1.4)})
                k += 1
    log(f"Danh mục: {len(products)} sản phẩm, {len(customers)} khách hàng")

    # ---------------- Hóa đơn bán hàng (nền) ----------------
    invoices: dict[str, dict] = {}
    lines: list[dict] = []
    inv_seq = [0]

    def new_invoice(cust, d, tag=""):
        inv_seq[0] += 1
        no = f"HD{d.strftime('%y%m')}-{inv_seq[0]:05d}"
        invoices[no] = {"SoHoaDon": no, "MaKH": cust["MaKH"], "MaCN": cust["MaCN"],
                        "NgayHoaDon": d, "LoaiKH": cust["LoaiKH"], "tag": tag}
        return no

    def add_line(no, prod, qty, disc_rate):
        gross = qty * prod["GiaNiemYet"]
        disc = round(gross * disc_rate)
        lines.append({"SoHoaDon": no, "MaSP": prod["MaSP"], "SoLuong": qty,
                      "DonGia": prod["GiaNiemYet"], "ThanhTienGop": gross, "ChietKhau": disc,
                      "DoanhThuThuan": gross - disc, "GiaVon": qty * prod["GiaVon"],
                      "MaNhom": prod["MaNhom"]})

    branch_scale = {b[0]: b[4] for b in BRANCHES}
    for m in range(1, 13):
        days = month_days(YEAR, m)
        for cust in customers:
            ctype = CUSTOMER_TYPES[cust["LoaiKH"]]
            n_same = ctype[0]
            monthly = (branch_scale[cust["MaCN"]] * ctype[1] / n_same * cust["weight"]
                       * SEASON[m] * rnd.uniform(0.92, 1.08))
            n_inv = max(1, ctype[3] + rnd.randint(-1, 1))
            for _ in range(n_inv):
                d = rnd.choice(days)
                no = new_invoice(cust, d)
                k_lines = rnd.randint(2, 5)
                groups = rnd.choices(list(GROUPS), weights=[25, 25, 25, 25], k=k_lines)
                for g in groups:
                    prod = rnd.choice(prod_by_group[g])
                    amt = monthly / n_inv / k_lines
                    # Q4: Miền Nam - đại lý tồn hàng Nhóm A sau đợt dồn hàng Q3 => mua ít Nhóm A
                    if (cust["MaCN"] == "CN03" and g == "A" and m >= 10
                            and cust["LoaiKH"].startswith("Đại lý")):
                        amt *= 0.55
                    qty = max(1, round(amt / prod["GiaNiemYet"]))
                    lo, hi = ctype[4]
                    disc = min(rnd.uniform(lo, hi), prod["TranChietKhau"])
                    # Chương trình CK vượt trần: Miền Nam - Nhóm A - Đại lý - Q3
                    if (cust["MaCN"] == "CN03" and g == "A" and 7 <= m <= 9
                            and cust["LoaiKH"].startswith("Đại lý")):
                        disc = SOUTH_PROMO_DISCOUNT
                    add_line(no, prod, qty, disc)
    log(f"Đã sinh {len(invoices)} hóa đơn nền, {len(lines)} dòng")

    def net_by_q() -> dict[int, float]:
        r = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
        for ln in lines:
            r[quarter_of(invoices[ln["SoHoaDon"]]["NgayHoaDon"])] += ln["DoanhThuThuan"]
        return r

    # ---------------- Dồn hàng Q3 Miền Nam (Nhóm A, CK 22%) ----------------
    base = net_by_q()
    need = base[2] * (1 + TARGET_REV_GROWTH) - base[3]
    south_dealers = [c for c in customers if c["MaCN"] == "CN03" and c["LoaiKH"].startswith("Đại lý")]
    plan = []  # (cust, date, prod, weight)
    for m in (7, 8, 9):
        days = month_days(YEAR, m)
        for c in south_dealers:
            w_type = 1.6 if c["LoaiKH"] == "Đại lý cấp 2" else 0.8
            for _ in range(2):
                d = rnd.choice(days)
                for prod in rnd.sample(prod_by_group["A"], 3):
                    plan.append((c, d, prod, w_type * c["weight"] * rnd.uniform(0.7, 1.3)))
    tw = sum(p[3] for p in plan)
    by_inv: dict[tuple, str] = {}
    for c, d, prod, w in plan:
        key = (c["MaKH"], d)
        if key not in by_inv:
            by_inv[key] = new_invoice(c, d, tag="promo_q3")
        net_unit = prod["GiaNiemYet"] * (1 - SOUTH_PROMO_DISCOUNT)
        qty = max(1, round(need * w / tw / net_unit))
        add_line(by_inv[key], prod, qty, SOUTH_PROMO_DISCOUNT)
    after = net_by_q()
    log("Doanh thu thuần theo quý (tỷ): " + ", ".join(f"Q{q}={v/1e9:.2f}" for q, v in after.items()))

    # ---------------- Công nợ & thu tiền ----------------
    inv_total = {}
    for ln in lines:
        inv_total[ln["SoHoaDon"]] = inv_total.get(ln["SoHoaDon"], 0) + ln["DoanhThuThuan"]
    cust_by_id = {c["MaKH"]: c for c in customers}
    ar = []  # mỗi dòng = 1 chứng từ phải thu
    # Số dư đầu kỳ (hóa đơn T11-T12/2024)
    for c in customers:
        ctype = CUSTOMER_TYPES[c["LoaiKH"]]
        for mm in (11, 12):
            amt = round(branch_scale[c["MaCN"]] * ctype[1] / ctype[0] * c["weight"] * 0.9 * rnd.uniform(0.8, 1.2), -3)
            d = date(2024, mm, rnd.randint(1, 28))
            no = f"DK{d.strftime('%y%m')}-{c['MaKH']}"
            ar.append({"SoChungTu": no, "MaKH": c["MaKH"], "MaCN": c["MaCN"], "NgayChungTu": d,
                       "SoTien": amt, "LoaiChungTu": "Số dư đầu kỳ"})
    for no, inv in invoices.items():
        ar.append({"SoChungTu": no, "MaKH": inv["MaKH"], "MaCN": inv["MaCN"], "NgayChungTu": inv["NgayHoaDon"],
                   "SoTien": inv_total[no], "LoaiChungTu": "Hóa đơn bán hàng"})
    END = date(YEAR, 12, 31)
    for r in ar:
        c = cust_by_id[r["MaKH"]]
        r["HanThanhToan"] = r["NgayChungTu"] + timedelta(days=c["SoNgayNo"])
        delay = max(-3, round(rnd.gauss(4, 6)))
        pay = r["HanThanhToan"] + timedelta(days=delay)
        if pay.weekday() == 6:
            pay += timedelta(days=1)
        r["NgayThanhToan"] = pay if pay <= END else None
        r["SoTienDaThu"] = r["SoTien"] if r["NgayThanhToan"] else 0
    # Một vài khoản nợ khó đòi lẻ tẻ (độ thực tế)
    for r in rnd.sample([x for x in ar if x["LoaiChungTu"] == "Hóa đơn bán hàng"], 6):
        r["NgayThanhToan"], r["SoTienDaThu"] = None, 0
    # Nợ quá hạn "nền" từ đầu năm (để mốc 30/06 không bằng 0)
    early = [x for x in ar if x["LoaiChungTu"] == "Hóa đơn bán hàng" and x["NgayChungTu"] <= date(YEAR, 2, 28)]
    for r in rnd.sample(early, 14):
        r["NgayThanhToan"], r["SoTienDaThu"] = None, 0

    # Bước 1: đại lý cấp 2 "rủi ro" ngừng trả các hóa đơn 15/03–20/05 (nợ xấu hình thành từ Q2)
    dealer2 = [c for c in customers if c["LoaiKH"] == "Đại lý cấp 2"]
    risky = set(c["MaKH"] for c in dealer2 if c["MaCN"] == "CN03")
    others = [c for c in dealer2 if c["MaCN"] != "CN03"]
    risky |= set(c["MaKH"] for c in rnd.sample(others, 5))
    for r in ar:
        if (r["MaKH"] in risky and r["LoaiChungTu"] == "Hóa đơn bán hàng"
                and date(YEAR, 3, 15) <= r["NgayChungTu"] <= date(YEAR, 5, 20) and rnd.random() < 0.75):
            r["NgayThanhToan"], r["SoTienDaThu"] = None, 0

    def cash_by_q() -> dict[int, float]:
        res = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
        for x in ar:
            if x["NgayThanhToan"] and x["NgayThanhToan"].year == YEAR:
                res[quarter_of(x["NgayThanhToan"])] += x["SoTienDaThu"]
        return res

    # Bước 2: hiệu chỉnh dòng tiền Q3 = Q2 x (1-12%) bằng cách đại lý cấp 2 ngưng/khất nợ
    cq = cash_by_q()
    target_q3 = cq[2] * (1 + TARGET_CASH_GROWTH)
    excess = cq[3] - target_q3
    cand = [x for x in ar if x["MaKH"] in {c["MaKH"] for c in dealer2} and x["NgayThanhToan"]
            and quarter_of(x["NgayThanhToan"]) == 3 and x["NgayThanhToan"].year == YEAR]
    cand.sort(key=lambda x: (x["MaKH"] not in risky, x["MaCN"] != "CN03", x["NgayChungTu"]))
    for x in cand:
        if excess <= 0:
            break
        if x["SoTienDaThu"] <= excess:
            excess -= x["SoTienDaThu"]
            late = rnd.random() < 0.35 and x["MaKH"] not in risky
            if late:  # khất sang Q4
                x["NgayThanhToan"] = date(YEAR, rnd.choice([11, 12]), rnd.randint(1, 28))
            else:
                x["NgayThanhToan"], x["SoTienDaThu"] = None, 0
        else:  # thu một phần để khớp chính xác
            x["SoTienDaThu"] = round(x["SoTienDaThu"] - excess)
            excess = 0
    if excess > 0:
        sys.exit(f"[generate] LỖI: không đủ công nợ đại lý cấp 2 để hiệu chỉnh dòng tiền (còn {excess:,.0f})")
    cq = cash_by_q()
    log("Dòng tiền thực thu theo quý (tỷ): " + ", ".join(f"Q{q}={v/1e9:.2f}" for q, v in cq.items()))

    # ---------------- Chi phí bán hàng & quản lý ----------------
    net_bm: dict[tuple, float] = {}
    gp_by_q = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    base_net_bm: dict[tuple, float] = {}
    for ln in lines:
        inv = invoices[ln["SoHoaDon"]]
        key = (inv["MaCN"], inv["NgayHoaDon"].month)
        net_bm[key] = net_bm.get(key, 0) + ln["DoanhThuThuan"]
        if inv["tag"] != "promo_q3":
            base_net_bm[key] = base_net_bm.get(key, 0) + ln["DoanhThuThuan"]
        gp_by_q[quarter_of(inv["NgayHoaDon"])] += ln["DoanhThuThuan"] - ln["GiaVon"]
    expenses = []
    for (cn, m), v in sorted(base_net_bm.items()):
        for typ, rate in OPEX_RATES.items():
            expenses.append({"Thang": date(YEAR, m, 1), "MaCN": cn, "LoaiChiPhi": typ,
                             "SoTien": round(v * rate * rnd.uniform(0.95, 1.05), -3)})

    def opex_q(q):
        return sum(e["SoTien"] for e in expenses if quarter_of(e["Thang"]) == q)

    np_q2 = gp_by_q[2] - opex_q(2)
    need_opex_q3 = gp_by_q[3] - np_q2 * (1 + TARGET_PROFIT_GROWTH)
    plug = need_opex_q3 - opex_q(3)
    if plug <= 0:
        sys.exit(f"[generate] LỖI: chi phí bù trừ Q3 âm ({plug:,.0f}) – cần chỉnh tham số")
    split = {"Khuyến mại & trưng bày": 0.70, "Vận chuyển & giao hàng": 0.30}
    for m in (7, 8, 9):
        for typ, sh in split.items():
            for e in expenses:
                if e["MaCN"] == "CN03" and e["Thang"].month == m and e["LoaiChiPhi"] == typ:
                    e["SoTien"] += plug * sh / 3
    for e in expenses:
        e["SoTien"] = round(e["SoTien"])
    log(f"Chi phí bù cho chương trình Miền Nam Q3: {plug/1e9:.2f} tỷ")

    # ---------------- KPI doanh thu ----------------
    net_bq: dict[tuple, float] = {}
    for (cn, m), v in net_bm.items():
        net_bq[(cn, (m - 1) // 3 + 1)] = net_bq.get((cn, (m - 1) // 3 + 1), 0) + v
    kpi_factor = {1: 1.02, 2: 0.99}
    kpis = []
    for cn, *_ in BRANCHES:
        q3k = net_bq[(cn, 2)] * 1.10
        vals = {1: net_bq[(cn, 1)] * kpi_factor[1], 2: net_bq[(cn, 2)] * kpi_factor[2],
                3: q3k, 4: q3k * 1.05}
        for q, v in vals.items():
            kpis.append({"Nam": YEAR, "Quy": q, "MaCN": cn, "KPIDoanhThu": round(v, -8)})

    # ---------------- Tồn kho ----------------
    qty_bpm: dict[tuple, int] = {}
    for ln in lines:
        inv = invoices[ln["SoHoaDon"]]
        key = (inv["MaCN"], ln["MaSP"], inv["NgayHoaDon"].month)
        qty_bpm[key] = qty_bpm.get(key, 0) + ln["SoLuong"]
    stock = []
    prod_by_id = {p["MaSP"]: p for p in products}
    for cn, *_ in BRANCHES:
        for p in products:
            open_q = round(qty_bpm.get((cn, p["MaSP"], 1), 0) * 0.9)
            for m in range(1, 13):
                out = qty_bpm.get((cn, p["MaSP"], m), 0)
                nxt = qty_bpm.get((cn, p["MaSP"], min(m + 1, 12)), out)
                end = max(0, round(nxt * rnd.uniform(0.8, 1.1)))
                if cn == "CN03" and p["MaNhom"] == "A" and m == 12:
                    end = round(end * 1.8)  # hàng tồn sau chương trình
                inn = max(0, end - open_q + out)
                end = open_q + inn - out
                stock.append({"Thang": date(YEAR, m, calendar.monthrange(YEAR, m)[1]), "MaCN": cn,
                              "MaSP": p["MaSP"], "TonDauKy": open_q, "NhapTrongKy": inn, "XuatTrongKy": out,
                              "TonCuoiKy": end, "GiaTriTonCuoiKy": end * prod_by_id[p["MaSP"]]["GiaVon"]})
                open_q = end

    # ---------------- Ghi SQLite ----------------
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript(SCHEMA_SQL)
    cur.executemany("INSERT INTO DanhMuc_ChiNhanh VALUES (?,?,?,?)", [b[:4] for b in BRANCHES])
    cur.executemany("INSERT INTO DanhMuc_SanPham VALUES (?,?,?,?,?,?,?,?)",
                    [(p["MaSP"], p["TenSP"], p["MaNhom"], p["TenNhom"], p["DonViTinh"], p["GiaNiemYet"],
                      p["GiaVon"], p["TranChietKhau"]) for p in products])
    cur.executemany("INSERT INTO DanhMuc_KhachHang VALUES (?,?,?,?,?,?)",
                    [(c["MaKH"], c["TenKH"], c["LoaiKH"], c["MaCN"], c["TinhThanh"], c["SoNgayNo"]) for c in customers])
    total_lines = len(lines)
    rows = []
    for i, ln in enumerate(lines, start=1):
        inv = invoices[ln["SoHoaDon"]]
        rows.append((ln["SoHoaDon"], inv["NgayHoaDon"].isoformat(), inv["MaCN"], inv["MaKH"], ln["MaSP"],
                     ln["SoLuong"], ln["DonGia"], ln["ThanhTienGop"], ln["ChietKhau"], ln["DoanhThuThuan"],
                     ln["GiaVon"]))
        if i % 10000 == 0 or i == total_lines:
            log(f"Ghi DoanhThu_HoaDon: {i}/{total_lines} ({i*100//total_lines}%)")
    cur.executemany("INSERT INTO DoanhThu_HoaDon VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    cur.executemany("INSERT INTO ChiPhi_BanHang VALUES (?,?,?,?)",
                    [(e["Thang"].isoformat(), e["MaCN"], e["LoaiChiPhi"], e["SoTien"]) for e in expenses])
    cur.executemany("INSERT INTO CongNo_KhachHang VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r["SoChungTu"], r["LoaiChungTu"], r["MaKH"], r["MaCN"], r["NgayChungTu"].isoformat(),
                      r["HanThanhToan"].isoformat(), r["SoTien"],
                      r["NgayThanhToan"].isoformat() if r["NgayThanhToan"] else None, r["SoTienDaThu"]) for r in ar])
    cur.executemany("INSERT INTO TonKho_SanPham VALUES (?,?,?,?,?,?,?,?)",
                    [(s["Thang"].isoformat(), s["MaCN"], s["MaSP"], s["TonDauKy"], s["NhapTrongKy"],
                      s["XuatTrongKy"], s["TonCuoiKy"], s["GiaTriTonCuoiKy"]) for s in stock])
    cur.executemany("INSERT INTO KPI_DoanhThu VALUES (?,?,?,?)",
                    [(k["Nam"], k["Quy"], k["MaCN"], k["KPIDoanhThu"]) for k in kpis])
    cur.execute("INSERT INTO Meta_DuLieu VALUES ('ten_cong_ty','Công ty CP Phân phối Hàng tiêu dùng Sao Việt (giả lập)')")
    cur.execute("INSERT INTO Meta_DuLieu VALUES ('ngay_chot_du_lieu', ?)", (END.isoformat(),))
    cur.execute("INSERT INTO Meta_DuLieu VALUES ('seed', ?)", (str(SEED),))
    cur.executescript(VIEWS_SQL)
    con.commit()
    cur.execute("VACUUM")
    con.close()
    log(f"Hoàn tất: {DB_PATH.relative_to(ROOT)} ({os.path.getsize(DB_PATH)/1e6:.1f} MB)")


SCHEMA_SQL = """
CREATE TABLE DanhMuc_ChiNhanh (MaCN TEXT PRIMARY KEY, TenCN TEXT NOT NULL, Vung TEXT, ThanhPho TEXT);
CREATE TABLE DanhMuc_SanPham (MaSP TEXT PRIMARY KEY, TenSP TEXT NOT NULL, MaNhom TEXT, TenNhom TEXT,
    DonViTinh TEXT, GiaNiemYet INTEGER, GiaVon INTEGER, TranChietKhau REAL);
CREATE TABLE DanhMuc_KhachHang (MaKH TEXT PRIMARY KEY, TenKH TEXT NOT NULL, LoaiKH TEXT, MaCN TEXT
    REFERENCES DanhMuc_ChiNhanh(MaCN), TinhThanh TEXT, SoNgayNo INTEGER);
CREATE TABLE DoanhThu_HoaDon (SoHoaDon TEXT, NgayHoaDon TEXT, MaCN TEXT, MaKH TEXT, MaSP TEXT,
    SoLuong INTEGER, DonGia INTEGER, ThanhTienGop INTEGER, ChietKhau INTEGER, DoanhThuThuan INTEGER, GiaVon INTEGER);
CREATE TABLE ChiPhi_BanHang (Thang TEXT, MaCN TEXT, LoaiChiPhi TEXT, SoTien INTEGER);
CREATE TABLE CongNo_KhachHang (SoChungTu TEXT PRIMARY KEY, LoaiChungTu TEXT, MaKH TEXT, MaCN TEXT,
    NgayChungTu TEXT, HanThanhToan TEXT, SoTien INTEGER, NgayThanhToan TEXT, SoTienDaThu INTEGER);
CREATE TABLE TonKho_SanPham (Thang TEXT, MaCN TEXT, MaSP TEXT, TonDauKy INTEGER, NhapTrongKy INTEGER,
    XuatTrongKy INTEGER, TonCuoiKy INTEGER, GiaTriTonCuoiKy INTEGER);
CREATE TABLE KPI_DoanhThu (Nam INTEGER, Quy INTEGER, MaCN TEXT, KPIDoanhThu INTEGER);
CREATE TABLE Meta_DuLieu (Khoa TEXT PRIMARY KEY, GiaTri TEXT);
CREATE INDEX ix_dt_ngay ON DoanhThu_HoaDon(NgayHoaDon);
CREATE INDEX ix_cn_kh ON CongNo_KhachHang(MaKH);
"""

# Các view "semantic-ready" – Semantic Layer chỉ được đọc từ v_*
VIEWS_SQL = """
CREATE VIEW v_sales AS
SELECT h.NgayHoaDon AS ngay,
       CAST(substr(h.NgayHoaDon,1,4) AS INTEGER) AS nam,
       (CAST(substr(h.NgayHoaDon,6,2) AS INTEGER)+2)/3 AS quy,
       CAST(substr(h.NgayHoaDon,6,2) AS INTEGER) AS thang,
       cn.TenCN AS chi_nhanh, sp.TenNhom AS nhom_hang, sp.TenSP AS san_pham,
       kh.LoaiKH AS loai_khach_hang, kh.TenKH AS khach_hang, kh.MaKH AS ma_kh, kh.TinhThanh AS tinh_thanh,
       sp.TranChietKhau AS tran_chiet_khau,
       h.SoLuong AS so_luong, h.ThanhTienGop AS thanh_tien_gop, h.ChietKhau AS chiet_khau,
       h.DoanhThuThuan AS doanh_thu_thuan, h.GiaVon AS gia_von
FROM DoanhThu_HoaDon h
JOIN DanhMuc_ChiNhanh cn ON cn.MaCN = h.MaCN
JOIN DanhMuc_SanPham sp ON sp.MaSP = h.MaSP
JOIN DanhMuc_KhachHang kh ON kh.MaKH = h.MaKH;

CREATE VIEW v_expense AS
SELECT e.Thang AS ngay, CAST(substr(e.Thang,1,4) AS INTEGER) AS nam,
       (CAST(substr(e.Thang,6,2) AS INTEGER)+2)/3 AS quy, CAST(substr(e.Thang,6,2) AS INTEGER) AS thang,
       cn.TenCN AS chi_nhanh, e.LoaiChiPhi AS loai_chi_phi, e.SoTien AS so_tien
FROM ChiPhi_BanHang e JOIN DanhMuc_ChiNhanh cn ON cn.MaCN = e.MaCN;

CREATE VIEW v_cash AS
SELECT c.NgayThanhToan AS ngay, CAST(substr(c.NgayThanhToan,1,4) AS INTEGER) AS nam,
       (CAST(substr(c.NgayThanhToan,6,2) AS INTEGER)+2)/3 AS quy, CAST(substr(c.NgayThanhToan,6,2) AS INTEGER) AS thang,
       cn.TenCN AS chi_nhanh, kh.LoaiKH AS loai_khach_hang, kh.TenKH AS khach_hang, c.SoTienDaThu AS so_tien_thu
FROM CongNo_KhachHang c
JOIN DanhMuc_ChiNhanh cn ON cn.MaCN = c.MaCN
JOIN DanhMuc_KhachHang kh ON kh.MaKH = c.MaKH
WHERE c.NgayThanhToan IS NOT NULL AND c.SoTienDaThu > 0;

CREATE VIEW v_kpi AS
SELECT k.Nam AS nam, k.Quy AS quy, cn.TenCN AS chi_nhanh, k.KPIDoanhThu AS kpi
FROM KPI_DoanhThu k JOIN DanhMuc_ChiNhanh cn ON cn.MaCN = k.MaCN;

-- Công nợ: dùng kèm tham số :asof (ngày chốt) trong Semantic Layer
CREATE VIEW v_ar AS
SELECT c.SoChungTu AS so_chung_tu, c.NgayChungTu AS ngay_chung_tu, c.HanThanhToan AS han_thanh_toan,
       c.NgayThanhToan AS ngay_thanh_toan, c.SoTien AS so_tien, c.SoTienDaThu AS so_tien_da_thu,
       cn.TenCN AS chi_nhanh, kh.LoaiKH AS loai_khach_hang, kh.TenKH AS khach_hang
FROM CongNo_KhachHang c
JOIN DanhMuc_ChiNhanh cn ON cn.MaCN = c.MaCN
JOIN DanhMuc_KhachHang kh ON kh.MaKH = c.MaKH;

CREATE VIEW v_inventory AS
SELECT t.Thang AS ngay, CAST(substr(t.Thang,1,4) AS INTEGER) AS nam,
       (CAST(substr(t.Thang,6,2) AS INTEGER)+2)/3 AS quy, CAST(substr(t.Thang,6,2) AS INTEGER) AS thang,
       cn.TenCN AS chi_nhanh, sp.TenNhom AS nhom_hang, sp.TenSP AS san_pham,
       t.TonCuoiKy AS ton_cuoi_ky, t.GiaTriTonCuoiKy AS gia_tri_ton
FROM TonKho_SanPham t
JOIN DanhMuc_ChiNhanh cn ON cn.MaCN = t.MaCN
JOIN DanhMuc_SanPham sp ON sp.MaSP = t.MaSP;
"""

if __name__ == "__main__":
    build()

"""Tạo file ví dụ đã điền sẵn theo file mẫu: chuỗi cà phê 3 cửa hàng, 01/2025–10/2026 (số giả lập, cố định).

    python tools/make_sample_data.py            # ghi data/samples/ChuoiCaPhe_ViDu.xlsx

Khách tải file này ở trang Dữ liệu của tôi để xem cách điền, hoặc nạp thử ngay.
"""
from __future__ import annotations

import io
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

from app.core import dataset  # noqa: E402

OUT = ROOT / "data" / "samples" / "ChuoiCaPhe_ViDu.xlsx"
STORES = (("Q1", "Cửa hàng Quận 1", "TP.HCM", 1.3), ("TD", "Cửa hàng Thủ Đức", "TP.HCM", 1.0),
          ("HK", "Cửa hàng Hoàn Kiếm", "Hà Nội", 0.8))
PRODUCTS = (("CF01", "Cà phê sữa đá", "Cà phê", "Cà phê pha", 29000, 9000),
            ("CF02", "Bạc xỉu", "Cà phê", "Cà phê pha", 35000, 11000),
            ("TR01", "Trà đào cam sả", "Trà", "Trà trái cây", 45000, 13000),
            ("BK01", "Bánh croissant", "Bánh", "Bánh ngọt", 39000, 17000),
            ("HB01", "Hạt cà phê 500g", "Hàng đóng gói", "Hạt rang", 180000, 95000))


def build() -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(dataset.template(pro=False)))
    info = wb["ThongTin"]
    info["B2"], info["B3"], info["B4"] = "Chuỗi Cà phê Ví Dụ", "10/2026", "Đại lý"
    for code, name, region, _ in STORES:
        wb["DonVi"].append([code, name, region, "Việt Nam"])
    hdr = {c.value: i for i, c in enumerate(wb["BanHang"][1])}
    rnd = random.Random(2026)
    d, n = date(2025, 1, 1), 0
    while d <= date(2026, 10, 31):
        for code, _, _, size in STORES:
            growth = 1.0 + (0.18 if d.year == 2026 else 0) + (0.12 if code == "TD" and d.year == 2026 else 0)
            for _ in range(int(rnd.randint(3, 6) * size * growth)):
                n += 1
                p = rnd.choice(PRODUCTS)
                q = rnd.randint(1, 4)
                if p[2] == "Hàng đóng gói":
                    ch = rnd.choice(("Tại quán", "Tại quán", "Giao hàng", "Đại lý"))
                else:
                    ch = rnd.choice(("Tại quán", "Giao hàng"))
                if ch == "Đại lý":
                    k = rnd.randint(1, 6)
                    cust = (f"DL{k:02d}", f"Đại lý {k}", "Đại lý")
                else:
                    cust = ("LE", "Khách lẻ", "Khách lẻ")
                disc = round(p[4] * q * (0.1 if ch == "Giao hàng" else 0))
                row = [None] * len(hdr)
                for h, v in (("Ngày", d.strftime("%d/%m/%Y")), ("Mã đơn vị", code), ("Kênh", ch),
                             ("Mã khách hàng", cust[0]), ("Tên khách hàng", cust[1]), ("Loại khách hàng", cust[2]),
                             ("Mã sản phẩm", p[0]), ("Tên sản phẩm", p[1]), ("Ngành hàng", p[2]),
                             ("Dòng sản phẩm", p[3]), ("Số chứng từ", f"HD{n:06d}"), ("Số lượng", q),
                             ("Doanh thu trước chiết khấu", p[4] * q), ("Chiết khấu", disc),
                             ("Doanh thu thuần", p[4] * q - disc), ("Giá vốn", p[5] * q)):
                    row[hdr[h]] = v
                wb["BanHang"].append(row)
        d += timedelta(days=1)
    for m in range(1, 13):
        for code, plan in (("Q1", 30e6), ("TD", 22e6), ("HK", 18e6)):
            wb["KeHoach"].append([2026, m, code, "Tại quán", plan])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build())
    print(f"Đã ghi {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")

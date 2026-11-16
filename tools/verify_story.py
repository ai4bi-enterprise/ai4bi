"""
Kiểm chứng "câu chuyện quản trị" Q3/2025 trong dữ liệu mẫu bằng SQL độc lập
(không đi qua Semantic Layer) – dùng để đối soát chéo kết quả của Chatbot.

Chạy: python tools/verify_story.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Console Windows mặc định dùng cp1252 -> in tiếng Việt sẽ lỗi UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

DB = Path(__file__).resolve().parents[1] / "sample_data" / "enterprise_sample.db"


def q(con, sql, *args):
    return con.execute(sql, args).fetchall()


def story(con) -> dict:
    rev = dict(q(con, "SELECT quy, SUM(doanh_thu_thuan) FROM v_sales WHERE nam=2025 GROUP BY quy"))
    gp = dict(q(con, "SELECT quy, SUM(doanh_thu_thuan-gia_von) FROM v_sales WHERE nam=2025 GROUP BY quy"))
    opex = dict(q(con, "SELECT quy, SUM(so_tien) FROM v_expense WHERE nam=2025 GROUP BY quy"))
    cash = dict(q(con, "SELECT quy, SUM(so_tien_thu) FROM v_cash WHERE nam=2025 GROUP BY quy"))
    kpi = dict(q(con, "SELECT quy, SUM(kpi) FROM v_kpi WHERE nam=2025 GROUP BY quy"))
    np_ = {k: gp[k] - opex[k] for k in gp}

    def overdue90(asof, ctype):
        return q(con, """SELECT COALESCE(SUM(so_tien - CASE WHEN ngay_thanh_toan <= ? THEN so_tien_da_thu ELSE 0 END),0)
                         FROM v_ar WHERE ngay_chung_tu <= ? AND loai_khach_hang = ?
                         AND julianday(?) - julianday(han_thanh_toan) > 90""", asof, asof, ctype, asof)[0][0]

    south_a = q(con, """SELECT quy, SUM(chiet_khau)*1.0/SUM(thanh_tien_gop), SUM(doanh_thu_thuan-gia_von)*1.0/SUM(doanh_thu_thuan)
                        FROM v_sales WHERE chi_nhanh='Miền Nam' AND nhom_hang LIKE 'Nhóm A%' AND quy IN (2,3) GROUP BY quy""")
    return {
        "rev": rev, "np": np_, "cash": cash, "kpi": kpi,
        "rev_g": rev[3] / rev[2] - 1, "np_g": np_[3] / np_[2] - 1, "cash_g": cash[3] / cash[2] - 1,
        "kpi_q3": rev[3] / kpi[3],
        "south_a": {r[0]: (r[1], r[2]) for r in south_a},
        "d2_90_q2": overdue90("2025-06-30", "Đại lý cấp 2"),
        "d2_90_q3": overdue90("2025-09-30", "Đại lý cấp 2"),
    }


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    s = story(con)
    t = lambda v: f"{v/1e9:,.2f} tỷ"  # noqa: E731
    print("=== CÂU CHUYỆN QUẢN TRỊ Q3/2025 (đối soát độc lập) ===")
    print(f"Doanh thu thuần  Q2={t(s['rev'][2])}  Q3={t(s['rev'][3])}  => {s['rev_g']:+.1%}  | Đạt KPI Q3: {s['kpi_q3']:.1%}")
    print(f"Lợi nhuận thuần  Q2={t(s['np'][2])}  Q3={t(s['np'][3])}  => {s['np_g']:+.1%}")
    print(f"Thực thu tiền    Q2={t(s['cash'][2])}  Q3={t(s['cash'][3])}  => {s['cash_g']:+.1%}")
    for k, (ck, gm) in s["south_a"].items():
        print(f"Miền Nam - Nhóm A Q{k}: tỷ lệ CK {ck:.1%} (trần 10%), biên LN gộp {gm:.1%}")
    print(f"Nợ quá hạn >90 ngày - Đại lý cấp 2: 30/06={t(s['d2_90_q2'])}  30/09={t(s['d2_90_q3'])}")
    ok = (abs(s["rev_g"] - 0.18) < 0.005 and abs(s["np_g"] + 0.12) < 0.005 and abs(s["cash_g"] + 0.12) < 0.005
          and s["kpi_q3"] > 1 and s["south_a"][3][0] > 0.10 and s["d2_90_q3"] > 3 * max(s["d2_90_q2"], 1))
    print("KẾT QUẢ:", "ĐẠT ✅" if ok else "KHÔNG ĐẠT ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

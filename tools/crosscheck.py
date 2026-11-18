"""Đối soát + kiểm chất lượng dữ liệu AI4BI – CHỈ ĐỌC (Blueprint 9, app/core/recon.py, app/core/dq.py).

    python tools/crosscheck.py                       # dữ liệu mẫu, kỳ = tháng chốt gần nhất (bi_meta)
    python tools/crosscheck.py --db user             # "Dữ liệu của tôi" đã nạp trên máy
    python tools/crosscheck.py --db neon             # kho theo DATABASE_URL (.env.local) – phiên chỉ đọc
    python tools/crosscheck.py --year 2026 --month 6 --scope region --xlsx doisoat.xlsx

Mã thoát: 0 = không có ❌, 1 = có ❌, 2 = không chạy được. Không sửa dữ liệu; kết quả không chứa tên khách hàng.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ro_sqlite(path: Path) -> str:
    return f"sqlite:///file:{path.resolve().as_posix()}?mode=ro&uri=true"


def setup(db: str) -> None:
    """Chọn kho TRƯỚC khi import app (config đọc biến môi trường lúc import)."""
    os.environ["USER_UPLOAD"] = "off"   # không để active.txt đổi kho ngầm
    if db == "demo":
        os.environ["DATABASE_URL"] = _ro_sqlite(ROOT / "data" / "aimoingay_demo.db")
    elif db == "user":
        p = Path(os.getenv("USER_DATA_DIR", ROOT / "data" / "user")) / "user_data.db"
        if not p.exists():
            sys.exit("Chưa có dữ liệu nào được nạp ở trang Dữ liệu của tôi.")
        os.environ["DATABASE_URL"] = _ro_sqlite(p)


def readonly() -> None:
    from sqlalchemy import event

    from app.core import db
    eng = db.engine()
    if eng.dialect.name == "postgresql":
        @event.listens_for(eng, "connect")
        def _ro(dbapi_con, _rec):
            cur = dbapi_con.cursor()
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cur.close()


def fmt_cell(v) -> str:
    if isinstance(v, float):
        return f"{v:,.0f}".replace(",", ".") if abs(v) >= 100 else f"{v:,.2f}"
    return "" if v is None else str(v)


def print_report(period, checks, show: int) -> None:
    from app.core.recon import ICON
    print(f"\nĐỐI SOÁT + CHẤT LƯỢNG DỮ LIỆU – kỳ {period.label}\n")
    layer = None
    for c in checks:
        if c.layer != layer:
            layer = c.layer
            print(f"== {layer}")
        print(f"  {ICON[c.status]}  {c.name}: {c.detail}")
        if c.rows and c.status in ("fail", "warn") and show:
            cols = list(c.rows[0])
            print("       " + " | ".join(cols))
            for r in c.rows[:show]:
                print("       " + " | ".join(fmt_cell(r.get(k)) for k in cols))
            if len(c.rows) > show:
                print(f"       … còn {len(c.rows) - show} dòng (xem --xlsx)")
    counts = {s: sum(c.status == s for c in checks) for s in ICON}
    print("\nTổng: " + "  ".join(f"{ICON[s]} {n}" for s, n in counts.items() if n))


def write_xlsx(path: str, period, checks) -> None:
    import openpyxl
    from openpyxl.styles import Font
    from app.core.recon import ICON
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tong hop"
    ws.append([f"Đối soát + chất lượng dữ liệu – kỳ {period.label}"])
    ws.append(["#", "Lớp", "Kiểm tra", "Kết quả", "Chi tiết", "Số dòng lệch"])
    for c in ws[2]:
        c.font = Font(bold=True)
    for i, c in enumerate(checks, start=1):
        ws.append([i, c.layer, c.name, ICON[c.status], c.detail, len(c.rows)])
        if c.rows:
            sh = wb.create_sheet(f"{i:02d}")
            sh.append([f"{c.layer} – {c.name}"])
            cols = list(c.rows[0])
            sh.append(cols)
            for r in c.rows:
                sh.append([r.get(k) for k in cols])
    for col, w in zip("ABCDEF", (5, 16, 60, 9, 80, 12)):
        ws.column_dimensions[col].width = w
    wb.save(path)
    print(f"Đã ghi {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Đối soát + kiểm chất lượng dữ liệu AI4BI (chỉ đọc)")
    ap.add_argument("--db", choices=("demo", "user", "neon"), default="demo")
    ap.add_argument("--year", type=int)
    ap.add_argument("--month", type=int)
    ap.add_argument("--scope", choices=("total", "region", "all"), default="all",
                    help="lớp 5: toàn công ty / + khu vực / + từng đơn vị")
    ap.add_argument("--xlsx", help="ghi báo cáo Excel (không chứa tên khách hàng)")
    ap.add_argument("--show", type=int, default=5, help="số dòng lệch in ra mỗi kiểm tra")
    a = ap.parse_args()
    setup(a.db)
    from app.core import recon
    from app.core.periods import make
    if a.db == "neon" and recon.db.engine().dialect.name == "sqlite":
        print("DATABASE_URL không trỏ tới PostgreSQL – kiểm tra .env.local", file=sys.stderr)
        return 2
    readonly()
    if bool(a.year) != bool(a.month):
        ap.error("--year và --month đi cùng nhau")
    period = make(a.year, a.month) if a.year else None
    period, checks = recon.run_all(period, a.scope)
    print_report(period, checks, a.show)
    if a.xlsx:
        write_xlsx(a.xlsx, period, checks)
    return 1 if recon.worst(checks) == recon.FAIL else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())

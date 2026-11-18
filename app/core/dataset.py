"""Dữ liệu của tôi – khách tải AI4BI về máy, nạp file Excel theo template, dashboard + chatbot chạy trên số của họ.

Nguyên tắc (Blueprint P1, P6, P7, P11):
- ĐẶC TẢ TEMPLATE (SHEETS) là nguồn chân lý duy nhất: file mẫu tải về, bộ đọc và bộ kiểm đều sinh từ đây.
- Mỗi sheet ghi 1:1 vào một bảng hợp đồng bi_*; chỉ tách năm/quý/tháng từ ngày, không tự tính, không tự lọc
  (chỉ bỏ dòng trống và dòng "Tổng cộng" – giữ lại sẽ nhân đôi số).
- Lỗi thì báo rõ sheet / dòng / cột, KHÔNG tự sửa; còn lỗi thì không cho dùng dữ liệu.
- Đối soát: tổng từng sheet trong file = tổng trong CSDL sau khi nạp (sai số 1 đồng).
- Chỉ chạy trên máy khách (SQLite cục bộ): tắt khi triển khai (Vercel) hoặc khi DATABASE_URL trỏ kho riêng.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from app import config
from app.core import marker

log = logging.getLogger(__name__)

DEMO_DB = config.ROOT / "data" / "aimoingay_demo.db"
MAX_BYTES = 30 * 1024 * 1024
MAX_ROWS = 500_000
MAX_ERRORS = 30
TOL = 1.0                                   # sai số đối soát file -> CSDL (đồng)
MODULE_TABLE = {"sales": "bi_sales", "pnl": "bi_pnl", "bs": "bi_bs", "cashflow": "bi_cashflow",
                "inventory": "bi_inventory"}
_lock = threading.Lock()


# ------------------------------------------------------------------ đặc tả template
@dataclass(frozen=True)
class Col:
    header: str          # tiêu đề cột trong Excel
    field: str           # cột hợp đồng bi_*
    kind: str            # text | date | int | num | year | month | scenario
    required: bool = False
    note: str = ""


@dataclass(frozen=True)
class Sheet:
    name: str
    table: str | None
    title: str
    cols: tuple[Col, ...] = ()
    required: bool = False
    pro: bool = False
    line_table: str | None = None      # mã khoản mục phải có trong bảng danh mục này
    amount: str | None = None          # cột tiền dùng để đối soát tổng
    note: str = ""


YM = (Col("Năm", "year", "year", True), Col("Tháng", "month", "month", True))
ORG = Col("Mã đơn vị", "org_code", "text", True, "Phải có trong sheet DonVi (nếu có sheet DonVi)")

SHEETS: tuple[Sheet, ...] = (
    Sheet("ThongTin", None, "Thông tin chung", note="Cột A là tên mục, cột B là giá trị. Không bắt buộc."),
    Sheet("DonVi", "bi_org", "Danh mục đơn vị (công ty / chi nhánh / cửa hàng)", (
        Col("Mã đơn vị", "org_code", "text", True),
        Col("Tên đơn vị", "org_name", "text", True),
        Col("Khu vực", "region_name", "text", False, "Bỏ trống = 'Toàn công ty'"),
        Col("Tỉnh / quốc gia", "country", "text"),
    ), note="Không bắt buộc: thiếu sheet này thì mỗi mã đơn vị trong BanHang thành một đơn vị."),
    Sheet("BanHang", "bi_sales", "Doanh thu – mỗi dòng là một dòng hoá đơn", (
        Col("Ngày", "date", "date", True, "dd/mm/yyyy"),
        ORG,
        Col("Kênh", "channel", "text", False, "VD: Đại lý, Bán lẻ, Online. Bỏ trống = 'Chung'"),
        Col("Mã khách hàng", "customer_code", "text", True),
        Col("Tên khách hàng", "customer_name", "text", True),
        Col("Loại khách hàng", "customer_type", "text"),
        Col("Mã sản phẩm", "product_code", "text", True),
        Col("Tên sản phẩm", "product_name", "text", True),
        Col("Ngành hàng", "category", "text", False, "Bỏ trống = 'Khác'"),
        Col("Dòng sản phẩm", "subcategory", "text"),
        Col("Số chứng từ", "order_no", "text", True, "Số hoá đơn / đơn hàng – dùng để đếm số đơn"),
        Col("Số lượng", "quantity", "num"),
        Col("Doanh thu trước chiết khấu", "gross_amount", "num", False, "Bỏ trống = doanh thu thuần + chiết khấu"),
        Col("Chiết khấu", "discount_amount", "num", False, "Bỏ trống = 0"),
        Col("Doanh thu thuần", "net_revenue", "num", True, "Đồng, chưa VAT"),
        Col("Giá vốn", "cogs", "num", False, "Đồng. Thiếu thì không tính được lãi gộp"),
    ), required=True, amount="net_revenue"),
    Sheet("KeHoach", "bi_sales_plan", "Kế hoạch doanh thu theo tháng × đơn vị × kênh", (
        *YM, ORG,
        Col("Kênh", "channel", "text", False, "Phải trùng tên kênh ở BanHang. Bỏ trống = 'Chung'"),
        Col("Kế hoạch doanh thu", "plan_revenue", "num", True),
    ), amount="plan_revenue"),
    Sheet("KetQuaKD", "bi_pnl", "P&L quản trị theo tháng (dấu: + là thu, − là chi)", (
        *YM, ORG,
        Col("Kịch bản", "scenario", "scenario", True, "Thực tế hoặc Kế hoạch"),
        Col("Mã khoản mục", "line_code", "text", True, "Theo sheet DanhMuc (P&L)"),
        Col("Số tiền", "amount", "num", True, "Thu ghi số dương, chi phí ghi số âm"),
    ), pro=True, line_table="bi_pnl_lines", amount="amount"),
    Sheet("ChiPhi", "bi_cost_detail", "Chi tiết chi phí theo tài khoản (số dương)", (
        *YM, ORG,
        Col("Mã khoản mục", "line_code", "text", True, "GIA_VON, CP_BH, CP_QLDN, KHAU_HAO…"),
        Col("Mã tài khoản", "account_code", "text", True, "VD: 6411"),
        Col("Tên tài khoản", "account_name", "text", True),
        Col("Số tiền", "amount", "num", True),
    ), pro=True, line_table="bi_pnl_lines", amount="amount"),
    Sheet("CanDoiKT", "bi_bs", "Bảng cân đối – số dư cuối tháng (số dương theo bản chất)", (
        *YM, ORG,
        Col("Mã khoản mục", "line_code", "text", True, "Theo sheet DanhMuc (Cân đối)"),
        Col("Số dư", "amount", "num", True),
    ), pro=True, line_table="bi_bs_lines", amount="amount"),
    Sheet("LuuChuyenTien", "bi_cashflow", "Lưu chuyển tiền tệ theo tháng", (
        *YM, ORG,
        Col("Mã khoản mục", "line_code", "text", True, "Theo sheet DanhMuc (Dòng tiền)"),
        Col("Số tiền", "amount", "num", True, "Tiền vào dương, tiền ra âm"),
    ), pro=True, line_table="bi_cf_lines", amount="amount"),
    Sheet("TonKho", "bi_inventory", "Tồn kho – chụp cuối tháng theo mặt hàng × kho", (
        *YM,
        Col("Kho", "warehouse", "text", True),
        Col("Mã sản phẩm", "product_code", "text", True),
        Col("Tên sản phẩm", "product_name", "text", True),
        Col("Ngành hàng", "category", "text"),
        Col("Dòng sản phẩm", "subcategory", "text"),
        Col("Số lượng nhập", "units_in", "num"),
        Col("Số lượng xuất", "units_out", "num"),
        Col("Tồn cuối", "units_end", "num", True),
        Col("Đơn giá vốn", "unit_cost", "num"),
        Col("Giá trị tồn", "value_end", "num", True),
        Col("Tồn an toàn", "safety_stock", "num"),
        Col("Điểm đặt hàng", "reorder_point", "num"),
    ), pro=True, amount="value_end"),
)
INFO_KEYS = {"ten cong ty": "company_name", "thang chot so lieu": "last_closed_month",
             "kenh trong tam": "focus_channel"}
SCENARIO = {"thuc te": "actual", "actual": "actual", "th": "actual", "ke hoach": "plan", "plan": "plan", "kh": "plan"}
SUBTOTAL = re.compile(r"^(tong cong|tong|cong|total|grand total|subtotal)\b")


def sheets(pro: bool | None = None) -> list[Sheet]:
    pro = config.PRO if pro is None else pro
    return [s for s in SHEETS if pro or not s.pro]


# ------------------------------------------------------------------ chế độ dữ liệu
def enabled() -> bool:
    """Chỉ khi chạy trên máy (SQLite cục bộ) – không bao giờ trên bản triển khai nhiều người dùng."""
    if os.getenv("VERCEL") or os.getenv("USER_UPLOAD", "on").lower() == "off":
        return False
    return config.DATABASE_URL.startswith("sqlite:///")


def data_dir() -> Path:
    return Path(os.getenv("USER_DATA_DIR", config.ROOT / "data" / "user"))


def user_db() -> Path:
    return data_dir() / "user_data.db"


def _active_file() -> Path:
    return data_dir() / "active.txt"


def active() -> str:
    """'user' khi đang dùng dữ liệu khách đã nạp, ngược lại 'demo'."""
    if not enabled() or not user_db().exists():
        return "demo"
    try:
        return "user" if _active_file().read_text(encoding="utf-8").strip() == "user" else "demo"
    except OSError:
        return "demo"


def database_url() -> str:
    return f"sqlite:///{user_db().as_posix()}" if active() == "user" else config.DATABASE_URL


@lru_cache(maxsize=1)
def _available() -> frozenset[str]:
    from app.core import db
    out = set()
    for mod, table in MODULE_TABLE.items():
        try:
            if db.fetch(f"SELECT 1 AS x FROM {table} LIMIT 1"):  # noqa: S608 – tên bảng cố định
                out.add(mod)
        except Exception:  # noqa: BLE001 – kho khách chưa có view này
            pass
    return frozenset(out)


def has_data(module: str) -> bool:
    """Phân hệ không có dữ liệu thì ẩn khỏi menu, dashboard và chatbot (khách mới nạp mỗi file bán hàng)."""
    table = MODULE_TABLE.get(module)
    return table is None or module in _available()


def reset_caches() -> None:
    """Đổi bộ dữ liệu: đóng kết nối cũ, xoá mọi cache đọc dữ liệu (P9: cache theo phiên bản dữ liệu)."""
    from app.core import db, marker
    try:
        db.engine().dispose()
    except Exception:  # noqa: BLE001
        pass
    db.engine.cache_clear()
    marker.clear_derived()
    marker.reset()


def switch(to: str) -> None:
    if not enabled():
        raise UploadError("Chức năng nạp dữ liệu chỉ có khi chạy AI4BI trên máy của bạn.")
    if to == "user" and not user_db().exists():
        raise UploadError("Chưa có dữ liệu nào được nạp.")
    with _lock:
        data_dir().mkdir(parents=True, exist_ok=True)
        _active_file().write_text(to, encoding="utf-8")
        reset_caches()


def delete_user_data() -> None:
    with _lock:
        if active() == "user":
            _active_file().write_text("demo", encoding="utf-8")
        reset_caches()
        for p in (user_db(), data_dir() / "staging.db"):
            if p.exists():
                p.unlink()


def user_info() -> dict | None:
    if not user_db().exists():
        return None
    try:
        con = sqlite3.connect(user_db())
        try:
            meta = dict(con.execute("SELECT key, value FROM bi_meta").fetchall())
            counts = {s.table: con.execute(f"SELECT COUNT(*) FROM {s.table}").fetchone()[0]  # noqa: S608
                      for s in SHEETS if s.table}
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return {"company": meta.get("company_name"), "uploaded_at": meta.get("uploaded_at"), "file": meta.get("file_name"),
            "as_of": meta.get("as_of_date"), "rows": counts}


# ------------------------------------------------------------------ đọc giá trị
class UploadError(Exception):
    pass


def norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "d")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def parse_num(v):
    """Số kiểu Việt Nam (1.234.567,89), kiểu Anh (1,234,567.89), âm trong ngoặc (500), có ký hiệu tiền."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s or s in ("-", "–"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("đ", "").replace("₫", "").replace("VND", "").replace("vnd", "")
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "." in s:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    try:
        x = float(s)
    except ValueError:
        raise ValueError("không phải số") from None
    return -x if neg else x


def parse_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:     # số ngày kiểu Excel
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v).strip().split(" ")[0].split("T")[0]
    for f in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise ValueError("ngày không đúng dạng dd/mm/yyyy")


def parse_text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)                                             # mã 1001 Excel lưu thành 1001.0
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s or None


def parse_value(c: Col, v):
    if c.kind == "text":
        return parse_text(v)
    if c.kind == "date":
        return parse_date(v)
    if c.kind == "scenario":
        s = parse_text(v)
        if s is None:
            return None
        if norm(s) not in SCENARIO:
            raise ValueError("chỉ nhận 'Thực tế' hoặc 'Kế hoạch'")
        return SCENARIO[norm(s)]
    x = parse_num(v)
    if x is None:
        return None
    if c.kind in ("year", "month", "int"):
        if not float(x).is_integer():
            raise ValueError("phải là số nguyên")
        x = int(x)
        if c.kind == "year" and not 2000 <= x <= 2100:
            raise ValueError("năm không hợp lệ")
        if c.kind == "month" and not 1 <= x <= 12:
            raise ValueError("tháng phải từ 1 đến 12")
    return x


# ------------------------------------------------------------------ đọc file
def _read_tables(content: bytes, filename: str) -> dict[str, list[list]]:
    if len(content) > MAX_BYTES:
        raise UploadError(f"File quá lớn (tối đa {MAX_BYTES // 1024 // 1024} MB).")
    if content[:2] == b"PK":                                   # .xlsx là file zip
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001
            raise UploadError("Không đọc được file Excel. Hãy lưu lại dạng .xlsx rồi thử lại.") from None
        out = {}
        for ws in wb.worksheets:
            rows = []
            for r in ws.iter_rows(values_only=True):
                rows.append(list(r))
                if len(rows) > MAX_ROWS + 20:
                    raise UploadError(f"Sheet {ws.title} quá {MAX_ROWS:,} dòng.".replace(",", "."))
            out[ws.title] = rows
        wb.close()
        return out
    if content[:4] == b"\xd0\xcf\x11\xe0":
        raise UploadError("File .xls (Excel cũ) chưa được hỗ trợ. Mở bằng Excel rồi lưu lại dạng .xlsx.")
    for enc in ("utf-8-sig", "cp1258", "cp1252"):              # CSV = một sheet BanHang
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UploadError("Không nhận dạng được file. Hãy dùng file mẫu .xlsx.")
    sample = text[:5000]
    delim = ";" if sample.count(";") > sample.count(",") else "\t" if sample.count("\t") > sample.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    if len(rows) > MAX_ROWS + 20:
        raise UploadError(f"File quá {MAX_ROWS:,} dòng.".replace(",", "."))
    return {"BanHang": rows}


def _find_sheet(tables: dict, s: Sheet) -> list[list] | None:
    want = norm(s.name)
    for name, rows in tables.items():
        if norm(name).replace(" ", "") == want.replace(" ", "") or norm(name) == norm(s.title):
            return rows
    return None


def _header(rows: list[list], s: Sheet) -> tuple[int, dict[str, int]] | None:
    """Dòng tiêu đề = dòng đầu tiên (trong 10 dòng) khớp ít nhất 2 cột của sheet; khớp theo tên không dấu."""
    names = {}
    for c in s.cols:
        names[norm(c.header)] = c.field
        names[norm(c.field)] = c.field
    for i, r in enumerate(rows[:10]):
        found = {}
        for j, v in enumerate(r):
            f = names.get(norm(v))
            if f and f not in found:
                found[f] = j
        if len(found) >= min(2, len(s.cols)):
            return i, found
    return None


@dataclass
class Report:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sheets: list[dict] = field(default_factory=list)
    recon: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    more_errors: int = 0

    def err(self, msg: str) -> None:
        self.ok = False
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(msg)
        else:
            self.more_errors += 1

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        if self.more_errors:
            d["errors"] = self.errors + [f"… và {self.more_errors} lỗi khác cùng loại."]
        return d


def _cell(row: list, j: int):
    return row[j] if j < len(row) else None


def _parse_sheet(rows: list[list], s: Sheet, rep: Report) -> list[dict]:
    h = _header(rows, s)
    if not h:
        rep.err(f"Sheet {s.name}: không tìm thấy dòng tiêu đề. Hãy giữ nguyên dòng tiêu đề của file mẫu.")
        return []
    hi, pos = h
    missing = [c.header for c in s.cols if c.required and c.field not in pos]
    if missing:
        rep.err(f"Sheet {s.name}: thiếu cột bắt buộc {', '.join(missing)}.")
        return []
    out, skipped = [], 0
    for i, r in enumerate(rows[hi + 1:], start=hi + 2):
        vals = [_cell(r, j) for j in pos.values()]
        if all(v is None or str(v).strip() == "" for v in vals):
            continue
        first = next((v for v in vals if v is not None and str(v).strip()), "")
        if SUBTOTAL.match(norm(first)):
            skipped += 1                                       # dòng tổng phụ: bỏ, giữ lại sẽ nhân đôi số (P6)
            continue
        rec = {}
        for c in s.cols:
            if c.field not in pos:
                rec[c.field] = None
                continue
            try:
                rec[c.field] = parse_value(c, _cell(r, pos[c.field]))
            except ValueError as e:
                rep.err(f"Sheet {s.name}, dòng {i}, cột '{c.header}': {e} ({str(_cell(r, pos[c.field]))[:30]}).")
                rec[c.field] = None
                continue
            if c.required and rec[c.field] is None:
                rep.err(f"Sheet {s.name}, dòng {i}: thiếu '{c.header}'.")
        out.append(rec)
    if skipped:
        rep.warnings.append(f"Sheet {s.name}: bỏ {skipped} dòng tổng cộng.")
    return out


# ------------------------------------------------------------------ kiểm + nạp
def _line_codes(table: str) -> dict[str, str]:
    con = sqlite3.connect(DEMO_DB)
    try:
        return {r[0]: r[1] for r in con.execute(f"SELECT line_code, line_name FROM {table}")}  # noqa: S608
    finally:
        con.close()


def _schema() -> list[str]:
    con = sqlite3.connect(DEMO_DB)
    try:
        return [r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'bi_%' "
                                          "OR (type='index' AND sql IS NOT NULL)")]
    finally:
        con.close()


def _ym(d: date) -> tuple[int, int, int]:
    return d.year, (d.month - 1) // 3 + 1, d.month


def build(content: bytes, filename: str, today: date | None = None) -> tuple[Report, Path | None]:
    """Đọc + kiểm + nạp vào CSDL tạm (staging). Trả báo cáo; staging chỉ có khi không còn lỗi."""
    if not enabled():
        raise UploadError("Chức năng nạp dữ liệu chỉ có khi chạy AI4BI trên máy của bạn.")
    today = today or date.today()
    rep = Report()
    tables = _read_tables(content, filename)
    data: dict[str, list[dict]] = {}
    for s in sheets():
        rows = _find_sheet(tables, s)
        if rows is None:
            if s.required:
                rep.err(f"Thiếu sheet {s.name} ({s.title}).")
            continue
        if s.table is None:
            for r in rows:
                if r and norm(r[0]) in INFO_KEYS and len(r) > 1 and r[1] not in (None, ""):
                    rep.info[INFO_KEYS[norm(r[0])]] = parse_text(r[1])
            continue
        recs = _parse_sheet(rows, s, rep)
        if recs:
            data[s.name] = recs
        elif s.required and rep.ok:
            rep.err(f"Sheet {s.name} chưa có dòng dữ liệu nào.")
    if not config.PRO:
        extra = [s.name for s in SHEETS if s.pro and _find_sheet(tables, s) is not None]
        if extra:
            rep.warnings.append(f"Bản Lite bỏ qua sheet {', '.join(extra)} – P&L, Bảng cân đối, Dòng tiền, Tồn kho có ở bản Pro.")

    # đơn vị: sheet DonVi hoặc sinh từ mã đơn vị đang dùng
    used = {r["org_code"] for n in data for r in data[n] if r.get("org_code")}
    orgs = []
    if "DonVi" in data:
        seen = set()
        for r in data["DonVi"]:
            if r["org_code"] in seen:
                rep.err(f"Sheet DonVi: mã đơn vị {r['org_code']} bị trùng.")
            seen.add(r["org_code"])
            orgs.append(r)
        unknown = sorted(used - seen)
        if unknown:
            rep.err(f"Mã đơn vị chưa khai báo trong sheet DonVi: {', '.join(unknown[:10])}"
                    f"{' …' if len(unknown) > 10 else ''}.")
    else:
        orgs = [{"org_code": c, "org_name": c, "region_name": None, "country": None} for c in sorted(used)]
        if used:
            rep.warnings.append("Không có sheet DonVi – tên đơn vị lấy bằng mã đơn vị.")

    # kiểm nghiệp vụ (P11: báo, không tự sửa)
    sales = data.get("BanHang", [])
    if sales:
        fut = [r for r in sales if r["date"] and r["date"] > today]
        if fut:
            rep.warnings.append(f"BanHang: {len(fut)} dòng có ngày sau hôm nay (VD {fut[0]['date']:%d/%m/%Y}).")
        no_cogs = sum(1 for r in sales if r["cogs"] is None)
        if no_cogs:
            rep.warnings.append(f"BanHang: {no_cogs:,} dòng thiếu giá vốn – lãi gộp, biên lãi gộp chỉ tính trên các dòng có giá vốn."
                                .replace(",", "."))
        neg = sum(1 for r in sales if (r["net_revenue"] or 0) < 0)
        if neg:
            rep.warnings.append(f"BanHang: {neg} dòng doanh thu âm (hàng trả lại / điều chỉnh) – được giữ nguyên.")
        chans = {r["channel"] or "Chung" for r in sales}
        for r in data.get("KeHoach", []):
            if (r["channel"] or "Chung") not in chans:
                rep.warnings.append(f"KeHoach: kênh '{r['channel'] or 'Chung'}' không có trong BanHang – kế hoạch này sẽ không có thực tế để so.")
                break
    for s in sheets():
        if s.line_table and s.name in data:
            codes = _line_codes(s.line_table)
            bad = sorted({r["line_code"] for r in data[s.name] if r["line_code"] and r["line_code"] not in codes})
            if bad:
                rep.err(f"Sheet {s.name}: mã khoản mục không có trong danh mục: {', '.join(bad[:10])}. Xem sheet DanhMuc.")
    if "CanDoiKT" in data and rep.ok:
        _check_bs(data["CanDoiKT"], rep)
    if not rep.ok:
        return rep, None

    path = _write(data, orgs, rep, filename, content, today)
    return rep, path


def _check_bs(rows: list[dict], rep: Report) -> None:
    grp = {c: g for c, (g) in _bs_groups().items()}
    bal: dict[tuple, float] = {}
    for r in rows:
        k = (r["year"], r["month"], r["org_code"])
        g = grp.get(r["line_code"])
        bal[k] = bal.get(k, 0.0) + (r["amount"] or 0) * (1 if g == "A" else -1)
    off = [(k, v) for k, v in bal.items() if abs(v) > 1000]
    if off:
        (y, m, o), v = off[0]
        rep.warnings.append(f"CanDoiKT: {len(off)} kỳ × đơn vị chưa cân (Tài sản ≠ Nợ + Vốn), VD {o} {m:02d}/{y} "
                            f"lệch {v:,.0f} đồng.".replace(",", "."))


@lru_cache(maxsize=1)
def _bs_groups() -> dict[str, str]:
    con = sqlite3.connect(DEMO_DB)
    try:
        return dict(con.execute("SELECT line_code, grp FROM bi_bs_lines").fetchall())
    finally:
        con.close()


def _write(data: dict, orgs: list[dict], rep: Report, filename: str, content: bytes, today: date) -> Path:
    data_dir().mkdir(parents=True, exist_ok=True)
    path = data_dir() / "staging.db"
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        for sql in _schema():
            con.execute(sql)
        demo = sqlite3.connect(DEMO_DB)
        try:
            for t in ("bi_pnl_lines", "bi_bs_lines", "bi_cf_lines"):     # danh mục khoản mục chuẩn của hợp đồng
                rows = demo.execute(f"SELECT * FROM {t}").fetchall()  # noqa: S608
                con.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(rows[0]))})", rows)  # noqa: S608
        finally:
            demo.close()
        regions: dict[str, int] = {}
        for i, o in enumerate(orgs, start=1):
            rn = o.get("region_name") or "Toàn công ty"
            regions.setdefault(rn, len(regions) + 1)
            con.execute("INSERT INTO bi_org VALUES (?,?,?,?,?,?,?)",
                        (o["org_code"], o["org_name"], o.get("country"), f"R{regions[rn]:02d}", rn, "VND", i))
        for s in sheets():
            recs = data.get(s.name)
            if not recs or s.table in (None, "bi_org"):
                continue
            if s.table == "bi_sales":
                for r in recs:
                    r["year"], r["quarter"], r["month"] = _ym(r["date"])
                    r["date"] = r["date"].isoformat()
                    r["channel"] = r["channel"] or "Chung"
                    r["category"] = r["category"] or "Khác"
                    r["subcategory"] = r["subcategory"] or r["category"]
                    r["customer_type"] = r["customer_type"] or "Chưa phân loại"
                    r["discount_amount"] = r["discount_amount"] or 0.0
                    if r["gross_amount"] is None:
                        r["gross_amount"] = r["net_revenue"] + r["discount_amount"]
            else:
                for r in recs:
                    if "month" in r:
                        r["quarter"] = (r["month"] - 1) // 3 + 1
                    if s.table == "bi_sales_plan":
                        r["channel"] = r["channel"] or "Chung"
                    if s.table == "bi_inventory":
                        r["category"] = r["category"] or "Khác"
                        r["subcategory"] = r["subcategory"] or r["category"]
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({s.table})")]
            con.executemany(f"INSERT INTO {s.table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",  # noqa: S608
                            [tuple(r.get(c) for c in cols) for r in recs])
            # đối soát lớp file -> CSDL (P7)
            f_rows, f_sum = len(recs), sum((r.get(s.amount) or 0) for r in recs)
            d_rows, d_sum = con.execute(f"SELECT COUNT(*), COALESCE(SUM({s.amount}),0) FROM {s.table}").fetchone()  # noqa: S608
            ok = f_rows == d_rows and abs(f_sum - d_sum) <= TOL
            rep.recon.append({"sheet": s.name, "label": s.title, "rows_file": f_rows, "rows_db": d_rows,
                              "sum_file": f_sum, "sum_db": d_sum, "ok": ok})
            if not ok:
                rep.err(f"Đối soát {s.name}: file và CSDL lệch – dữ liệu không được dùng.")
            info = {"sheet": s.name, "label": s.title, "rows": f_rows, "total": f_sum}
            if s.table == "bi_sales":
                ds = sorted(r["date"] for r in recs)
                info.update(first=ds[0], last=ds[-1], orders=len({r["order_no"] for r in recs}),
                            customers=len({r["customer_code"] for r in recs}), products=len({r["product_code"] for r in recs}))
            elif "year" in recs[0]:
                ym = sorted(r["year"] * 100 + r["month"] for r in recs)
                info.update(first=f"{ym[0] % 100:02d}/{ym[0] // 100}", last=f"{ym[-1] % 100:02d}/{ym[-1] // 100}")
            rep.sheets.append(info)
        rep.sheets.insert(0, {"sheet": "DonVi", "label": "Đơn vị", "rows": len(orgs), "total": None})

        last = con.execute("SELECT MAX(date) FROM bi_sales").fetchone()[0]
        ym = con.execute("SELECT MAX(year*100+month) FROM bi_sales").fetchone()[0]
        lcm = rep.info.get("last_closed_month")
        if lcm:
            m = re.fullmatch(r"(\d{1,2})[/\-.](\d{4})", lcm) or re.fullmatch(r"(\d{4})[/\-.](\d{1,2})", lcm)
            if not m:
                rep.warnings.append("ThongTin: 'Tháng chốt số liệu' không đúng dạng MM/YYYY – dùng tháng cuối có doanh thu.")
                lcm = None
            else:
                a, b = (int(x) for x in m.groups())
                y_, m_ = (b, a) if a <= 12 and b > 12 else (a, b)
                lcm = f"{y_}-{m_:02d}" if 1 <= m_ <= 12 else None
        meta = {
            "company_name": rep.info.get("company_name") or "Doanh nghiệp của tôi", "currency": "VND",
            "as_of_date": last, "last_closed_month": lcm or f"{ym // 100}-{ym % 100:02d}",
            "source": f"Dữ liệu doanh nghiệp nạp từ file {Path(filename).name[:80]} ngày {today:%d/%m/%Y}",
            "transform_note": "Nạp nguyên giá trị từ file theo template AI4BI; chỉ tách năm/quý/tháng từ ngày, "
                              "bỏ dòng trống và dòng tổng cộng.",
            **(marker.stamp(lambda sql: con.execute(sql).fetchone()[0], _user_version()) or
               {"data_version": _user_version(), "data_loaded_at": _user_meta("data_loaded_at")}),
            "uploaded_at": datetime.now().isoformat(timespec="seconds"), "file_name": Path(filename).name[:120],
        }
        if rep.info.get("focus_channel"):
            meta["focus_channel"] = rep.info["focus_channel"]
        con.executemany("INSERT INTO bi_meta VALUES (?,?)", meta.items())
        con.commit()
    finally:
        con.close()
    if not rep.ok:
        path.unlink()
        return None
    return path


def _user_meta(key: str) -> str | None:
    if not user_db().exists():
        return None
    con = sqlite3.connect(f"file:{user_db().as_posix()}?mode=ro", uri=True)
    try:
        r = con.execute("SELECT value FROM bi_meta WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _user_version() -> str | None:
    """Mốc của dữ liệu khách đang có – nạp lại đúng nội dung cũ thì giữ phiên bản (Blueprint 10.4)."""
    return _user_meta("data_version")


def activate_staging() -> None:
    staging = data_dir() / "staging.db"
    if not staging.exists():
        raise UploadError("Chưa có file đã kiểm tra. Hãy tải file lên lại.")
    with _lock:
        reset_caches()                                        # Windows: đóng kết nối trước khi thay file
        os.replace(staging, user_db())
        _active_file().write_text("user", encoding="utf-8")
        reset_caches()


# ------------------------------------------------------------------ file mẫu
def template(pro: bool | None = None) -> bytes:
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    pro = config.PRO if pro is None else pro
    wb = openpyxl.Workbook()
    head = PatternFill("solid", fgColor="1F4E79")
    req = PatternFill("solid", fgColor="C00000")
    white = Font(bold=True, color="FFFFFF")
    ws = wb.active
    ws.title = "HuongDan"
    lines = [
        ("FILE MẪU NẠP DỮ LIỆU AI4BI", True),
        ("1. Điền dữ liệu vào các sheet bên dưới, GIỮ NGUYÊN dòng tiêu đề (dòng 1). Cột tiêu đề màu đỏ là bắt buộc.", False),
        ("2. Tiền ghi bằng đồng, chưa VAT, không làm tròn. Ngày ghi dd/mm/yyyy. Số kiểu 1.234.567 hoặc 1234567 đều được.", False),
        ("3. Chỉ sheet BanHang là bắt buộc. Sheet nào không dùng thì để trống hoặc xoá đi.", False),
        ("4. Không cần dòng tổng cộng – AI4BI tự cộng. Dòng bắt đầu bằng 'Tổng cộng' sẽ bị bỏ qua.", False),
        ("5. Mở AI4BI → Dữ liệu của tôi → Tải file lên. Hệ thống kiểm tra, đối soát tổng rồi mới cho dùng.", False),
        ("6. Dữ liệu chỉ nằm trên máy của bạn (thư mục data/user), không gửi ra ngoài.", False),
        ("", False),
    ]
    for s in sheets(pro):
        lines.append((f"Sheet {s.name}: {s.title}{' (BẮT BUỘC)' if s.required else ''}", True))
        if s.note:
            lines.append((f"   {s.note}", False))
        for c in s.cols:
            lines.append((f"   • {c.header}{' *' if c.required else ''}{' – ' + c.note if c.note else ''}", False))
    if not pro:
        lines.append(("Bản Pro có thêm sheet KetQuaKD, ChiPhi, CanDoiKT, LuuChuyenTien, TonKho.", True))
    for i, (t, b) in enumerate(lines, start=1):
        ws.cell(i, 1, t).font = Font(bold=b, size=13 if i == 1 else 11)
    ws.column_dimensions["A"].width = 130

    for s in sheets(pro):
        w = wb.create_sheet(s.name)
        if s.table is None:
            w.append(["Mục", "Giá trị"])
            w.append(["Tên công ty", ""])
            w.append(["Tháng chốt số liệu", ""])
            w.append(["Kênh trọng tâm", ""])
            w["A1"].font = w["B1"].font = white
            w["A1"].fill = w["B1"].fill = head
            w.column_dimensions["A"].width, w.column_dimensions["B"].width = 26, 40
            w["C3"] = "MM/YYYY – bỏ trống = tháng cuối có doanh thu"
            w["C4"] = "Kênh dùng để đếm khách hàng / top khách hàng (VD: Đại lý). Bỏ trống = mọi kênh"
            continue
        w.append([c.header for c in s.cols])
        for j, c in enumerate(s.cols, start=1):
            cell = w.cell(1, j)
            cell.font, cell.fill = white, req if c.required else head
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            if c.note:
                cell.comment = Comment(c.note, "AI4BI")
            w.column_dimensions[cell.column_letter].width = max(12, min(30, len(c.header) + 4))
            if c.kind == "scenario":
                dv = DataValidation(type="list", formula1='"Thực tế,Kế hoạch"', allow_blank=True)
                w.add_data_validation(dv)
                dv.add(f"{cell.column_letter}2:{cell.column_letter}100000")
        w.freeze_panes = "A2"
    if pro:
        w = wb.create_sheet("DanhMuc")
        w.append(["Nhóm", "Mã khoản mục", "Tên khoản mục", "Ghi chú"])
        for cell in w[1]:
            cell.font, cell.fill = white, head
        for t, label in (("bi_pnl_lines", "P&L / Chi phí"), ("bi_bs_lines", "Cân đối"), ("bi_cf_lines", "Dòng tiền")):
            con = sqlite3.connect(DEMO_DB)
            try:
                rows = con.execute(f"SELECT * FROM {t} ORDER BY sort_order").fetchall()  # noqa: S608
            finally:
                con.close()
            for r in rows:
                note = {"A": "Tài sản", "L": "Nợ phải trả", "E": "Vốn chủ sở hữu"}.get(r[2], r[2]) if len(r) == 4 else ""
                w.append([label, r[0], r[1], note])
        for col, wd in zip("ABCD", (16, 18, 44, 18)):
            w.column_dimensions[col].width = wd
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

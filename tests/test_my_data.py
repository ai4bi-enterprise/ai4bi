"""Bản Lite: khách nạp file Excel của họ -> Tổng quan, Doanh thu và chatbot hiện đúng số của họ. Chạy: pytest -q"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core import dataset
from app.main import app

COMPANY = "Cửa hàng Thử Nghiệm"


def workbook() -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(dataset.template()))
    wb["ThongTin"]["B2"] = COMPANY
    wb["DonVi"].append(["Q1", "Cửa hàng Quận 1", "TP.HCM"])
    wb["DonVi"].append(["Q3", "Cửa hàng Quận 3", "TP.HCM"])
    hdr = {c.value: i for i, c in enumerate(wb["BanHang"][1])}
    for d, org, net in (("02/04/2026", "Q1", "3.000.000"), ("15/04/2026", "Q3", "1.250.000"), ("20/04/2025", "Q1", "2.000.000")):
        row = [None] * len(hdr)
        for h, v in (("Ngày", d), ("Mã đơn vị", org), ("Mã khách hàng", "K1"), ("Tên khách hàng", "Khách lẻ"),
                     ("Mã sản phẩm", "S1"), ("Tên sản phẩm", "Cà phê"), ("Số chứng từ", d + org), ("Doanh thu thuần", net)):
            row[hdr[h]] = v
        wb["BanHang"].append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_DATA_DIR", str(tmp_path))
    dataset.reset_caches()
    with TestClient(app, base_url="https://testserver") as c:
        yield c
    dataset.reset_caches()


def test_lite_template_has_only_lite_sheets(client):
    r = client.get("/api/data/template")
    assert r.status_code == 200
    names = openpyxl.load_workbook(io.BytesIO(r.content)).sheetnames
    assert "BanHang" in names and "KetQuaKD" not in names


def test_lite_upload_shows_my_numbers(client):
    assert client.get("/api/meta").json()["data"] == {"upload": True, "active": "demo"}
    r = client.post("/api/data/upload", content=workbook(), headers={"x-file-name": "cua_hang.xlsx"}).json()
    assert r["can_activate"] and r["errors"] == [], r
    client.post("/api/data/activate")
    m = client.get("/api/meta").json()
    assert m["company"] == COMPANY and m["latest"] == {"year": 2026, "month": 4}
    d = client.get("/api/dashboard/sales?year=2026&month=4&mode=month").json()
    rev = next(k for k in d["kpis"] if k["key"] == "net_revenue")
    assert rev["value"] == 4_250_000
    assert next(x for x in rev["compares"] if x["label"] == "Cùng kỳ")["base"] == 2_000_000
    a = client.post("/api/chat/ask", json={"question": "Doanh thu tháng 4 năm 2026"}).json()
    assert next(k for k in a["kpis"] if k["key"] == "net_revenue")["value"] == 4_250_000
    client.delete("/api/data/mine")
    assert client.get("/api/meta").json()["company"] != COMPANY

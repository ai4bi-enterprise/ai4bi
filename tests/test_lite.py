"""Kiểm thử bản AI4BI Lite. Chạy: pytest -q"""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from app import config
from app.chat import gate, nlu
from app.chat.engine import answer
from app.core.periods import make
from app.core.scope import users
from app.main import app
from app.modules import REGISTRY

P = make(2026, 9, "quarter")
CTX = {"year": 2026, "month": 9, "mode": "quarter", "latest_year": 2026, "latest_month": 9}
CEO = users()["ceo"]
LOCKED = ("pnl", "bs", "cashflow", "inventory")


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_edition_is_lite_without_login(client):
    assert config.EDITION == "lite" and config.AUTH_MODE == "demo"
    assert client.get("/api/health").json()["edition"] == "lite"
    for path in ("/login", "/admin/users", "/api/auth/me"):
        assert client.get(path).status_code == 404


def test_pro_modules_absent():
    assert set(REGISTRY) == {"overview", "sales"}
    for mod in ("app.auth_api", "app.core.auth", "app.core.secdb", "app.core.llmkeys"):
        assert importlib.util.find_spec(mod) is None, mod


def test_menu_shows_pro_items_locked(client):
    items = {i["key"]: i for g in client.get("/api/meta").json()["menu"] for i in g["items"]}
    assert not items["sales"]["locked"] and not items["overview"]["locked"]
    assert all(items[k]["locked"] for k in LOCKED)


@pytest.mark.parametrize("module", ["overview", "sales"])
def test_dashboard_works(client, module):
    r = client.get(f"/api/dashboard/{module}", params={"year": 2026, "month": 9, "mode": "quarter"})
    assert r.status_code == 200 and r.json()["kpis"]


@pytest.mark.parametrize("module", LOCKED)
def test_pro_dashboard_needs_upgrade(client, module):
    r = client.get(f"/api/dashboard/{module}", params={"year": 2026, "month": 9, "mode": "quarter"})
    assert r.status_code == 402


def test_overview_has_no_pro_kpis():
    keys = {k["module"] for k in REGISTRY["overview"].build(P, CEO, {})["kpis"]}
    assert keys == {"sales"}


def test_chat_equals_dashboard():  # Blueprint P2: chatbot = dashboard
    dash = next(k for k in REGISTRY["sales"].build(P, CEO, {})["kpis"] if k["key"] == "net_revenue")
    chat = next(k for k in answer("Doanh thu quý 3", CTX, CEO)["kpis"] if k["key"] == "net_revenue")
    assert chat["display"] == dash["display"]


def test_chat_pro_metric_gives_upgrade_note():
    res = answer("Lợi nhuận sau thuế quý 3", CTX, CEO)
    assert not any(k["key"] == "npat" for k in res["kpis"])
    assert "AI4BI Pro" in res["answer"] and "Không có dữ liệu" not in res["answer"]


@pytest.mark.parametrize("q,intent,metric,dim", [
    ("Doanh thu theo khu vực năm 2025", "breakdown", "net_revenue", "region"),
    ("Top 5 đại lý doanh thu cao nhất", "rank", "net_revenue", "customer"),
])
def test_nlu(q, intent, metric, dim):
    p = nlu.parse(q, CTX)[0]
    assert p["intent"] == intent and metric in p["metrics"]
    assert (p["dims"][:1] or [None])[0] == dim


def test_gate_blocks_invented_numbers():  # lời văn LLM chỉ được dùng số có trong bằng chứng
    ev = "Doanh thu Q3/2026 = 300,4 tỷ; +65,3% so cùng kỳ"
    assert gate.check("Doanh thu đạt 300,4 tỷ, tăng +65,3%.", ev) == []
    assert gate.check("Doanh thu đạt 310 tỷ.", ev)

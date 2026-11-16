"""Kiểm thử tự động: chạy `pytest -q` trước mỗi lần push."""
import copy
import dataclasses
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app.config import LLMProvider, load_settings  # noqa: E402
from app.engine.bod_demo import FOLLOWUPS  # noqa: E402
from app.engine.compiler import IntentError  # noqa: E402
from app.engine.llm_router import LLMError, LLMRouter, extract_json  # noqa: E402
from app.engine.pipeline import MORE_PROMPTS, QUICK_PROMPTS, Pipeline  # noqa: E402
from app.main import app  # noqa: E402
import verify_story  # noqa: E402

P = Pipeline(load_settings())
client = TestClient(app)


def run(q, **kw):
    raw = P.rules.parse(q)
    raw.update(kw)
    return P.compiler.run(P.compiler.validate(raw))


def insights_of(res):
    return next(b for b in res["blocks"] if b["type"] == "insights")


def story():
    con = sqlite3.connect(f"file:{verify_story.DB}?mode=ro", uri=True)
    return verify_story.story(con)


# ============================================================ dữ liệu & Semantic Layer
def test_hidden_story_q3():
    s = story()
    assert abs(s["rev_g"] - 0.18) < 0.005
    assert abs(s["np_g"] + 0.12) < 0.005
    assert abs(s["cash_g"] + 0.12) < 0.005
    assert s["kpi_q3"] > 1
    assert s["south_a"][3][0] > 0.10 >= s["south_a"][2][0]
    assert s["d2_90_q3"] > 3 * s["d2_90_q2"]


def test_semantic_layer_matches_independent_sql():
    s = story()
    r = run("Doanh thu quý 3 so với quý trước")
    assert r.rows[0]["doanh_thu_thuan"] == s["rev"][3]
    assert r.rows[0]["doanh_thu_thuan__prev"] == s["rev"][2]
    r = run("Lợi nhuận thuần theo quý")
    assert {x["quy"]: x["loi_nhuan_thuan"] for x in r.rows}[3] == s["np"][3]
    r = run("Dòng tiền thực thu quý 2")
    assert r.rows[0]["thuc_thu"] == s["cash"][2]


@pytest.mark.parametrize("q,metric,dims", [
    ("Doanh thu và biên lợi nhuận gộp theo từng chi nhánh trong Quý 3?", "doanh_thu_thuan", ["chi_nhanh"]),
    ("Top 5 khách hàng đại lý có công nợ quá hạn cao nhất hiện tại?", "no_qua_han", ["khach_hang"]),
    ("Tỷ lệ đạt KPI theo chi nhánh quý 3", "ty_le_dat_kpi", ["chi_nhanh"]),
    ("Nợ quá hạn trên 90 ngày theo loại khách hàng theo quý", "no_qua_han_90", ["quy", "loai_khach_hang"]),
    ("Tỷ lệ chiết khấu Nhóm A Miền Nam theo tháng", "ty_le_chiet_khau", ["thang"]),
    # 2 lỗi router đã sửa ở v1.1
    ("Nợ quá hạn >90 ngày của Đại lý cấp 2 theo khách hàng", "no_qua_han_90", ["khach_hang"]),
    ("Chi phí bán hàng theo loại chi phí quý 3", "chi_phi_ban_hang", ["loai_chi_phi"]),
])
def test_router_and_compiler(q, metric, dims):
    r = run(q)
    assert r.metrics[0] == metric
    assert r.dims == dims
    assert r.rows


def test_router_metric_phrase_is_not_aging_filter():
    raw = P.rules.parse("Nợ quá hạn >90 ngày của Đại lý cấp 2 theo khách hàng")
    assert "nhom_tuoi_no" not in raw["filters"]
    assert raw["filters"]["loai_khach_hang"] == ["Đại lý cấp 2"]


def test_top5_overdue_dealers_limit_and_filter():
    r = run("Top 5 khách hàng đại lý có công nợ quá hạn cao nhất hiện tại?")
    assert len(r.rows) == 5
    vals = [x["no_qua_han"] for x in r.rows]
    assert vals == sorted(vals, reverse=True)
    assert r.intent.filters["loai_khach_hang"] == ["Đại lý cấp 1", "Đại lý cấp 2"]


def test_guard_rejects_unknown_values_and_metrics():
    with pytest.raises(IntentError):
        P.compiler.validate({"metrics": ["doanh_thu_thuan"], "filters": {"chi_nhanh": ["Miền Tây'; DROP TABLE x;--"]}})
    with pytest.raises(IntentError):
        P.compiler.validate({"metrics": ["ebitda_bi_a"]})
    with pytest.raises(IntentError):  # chi phí không có chiều nhóm hàng
        P.compiler.validate({"metrics": ["chi_phi_ban_hang"], "dimensions": ["nhom_hang"]})


def test_database_is_read_only():
    with pytest.raises(sqlite3.OperationalError):
        P.db.query("DELETE FROM DoanhThu_HoaDon")


def test_api_endpoints_and_bod_demo():
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert len(client.get("/api/v1/suggestions").json()["quick"]) == 4
    res = client.post("/api/v1/chat", json={"question": QUICK_PROMPTS[2]["text"]}).json()
    assert res["mode"] == "bod_demo"
    assert any(b["type"] == "callout" for b in res["blocks"])
    res = client.post("/api/v1/chat", json={"question": "Doanh thu theo chi nhánh", "bod_mode": False}).json()
    assert res["mode"] == "descriptive" and res["sql"]
    assert client.get("/").status_code == 200


# ============================================================ phân tích (Nhận xét · Đánh giá · …)
ALL_QUESTIONS = [q["text"] for q in QUICK_PROMPTS[:2]] + MORE_PROMPTS + [
    "Doanh thu theo chi nhánh", "Chi phí bán hàng theo loại chi phí quý 3",
    "Dòng tiền thực thu theo loại khách hàng Quý 3 so với quý trước", "Doanh thu tháng 8",
]


@pytest.mark.parametrize("q", ALL_QUESTIONS)
def test_every_answer_has_five_sections(q):
    res = P.answer(q)
    assert res["mode"] == "descriptive"
    ins = insights_of(res)
    keys = [s["key"] for s in ins["sections"]]
    assert keys == ["nhan_xet", "danh_gia", "ket_luan", "de_xuat"]
    assert all(s["items"] for s in ins["sections"])
    assert ins["status"] in ("good", "neutral", "warn", "bad", "none")
    assert "không dùng LLM" in ins["basis"]
    assert ins["suggestions"], "phải có ít nhất 1 câu gợi ý"


@pytest.mark.parametrize("q", ALL_QUESTIONS)
def test_every_suggestion_is_answerable(q):
    for s in insights_of(P.answer(q))["suggestions"]:
        res = P.answer(s)
        assert res["mode"] == "descriptive", f"Gợi ý không chạy được: {s}"


@pytest.mark.parametrize("scenario", list(FOLLOWUPS))
def test_bod_followups_are_answerable(scenario):
    for s in FOLLOWUPS[scenario]:
        assert P.answer(s)["mode"] == "descriptive", s


def test_bod_demo_has_insight_sections():
    res = P.answer(QUICK_PROMPTS[2]["text"])
    ins = insights_of(res)
    assert [s["key"] for s in ins["sections"]] == ["nhan_xet", "danh_gia", "ket_luan", "de_xuat"]
    assert ins["suggestions"] == FOLLOWUPS["cash_paradox"]


def test_insight_numbers_match_independent_sql():
    s = story()
    ins = insights_of(P.answer("Doanh thu và biên lợi nhuận gộp theo từng chi nhánh trong Quý 3?"))
    obs = " ".join(i["text"] for i in ins["sections"][0]["items"])
    assert f"{s['rev'][3] / 1e9:,.2f}".replace(".", ",") in obs          # 142,41
    assert f"{(s['rev_g']) * 100:.1f}".replace(".", ",") + "%" in obs    # 18,0%
    assert ins["status"] == "bad"                                         # biên LN gộp dưới ngưỡng cảnh báo
    acts = " ".join(i["text"] for i in ins["sections"][3]["items"])
    assert "Miền Nam" in acts


def test_assessment_follows_yaml_thresholds():
    m = P.catalog.metrics["ty_le_dat_kpi"]
    old = copy.deepcopy(m.assess)
    try:
        q = "Tỷ lệ đạt KPI theo chi nhánh Quý 3"
        assert insights_of(P.answer(q))["status"] == "bad"
        m.assess = {"good": 0.5, "warn": 0.4}           # nới ngưỡng => không còn chi nhánh nào cảnh báo về mức
        texts = " ".join(i["text"] for i in insights_of(P.answer(q))["sections"][1]["items"])
        assert "dưới ngưỡng" not in texts
    finally:
        m.assess = old


def test_insights_are_deterministic():
    q = "Phân tích tuổi nợ Đại lý cấp 2"
    a, b = insights_of(P.answer(q)), insights_of(P.answer(q))
    assert a == b


def test_aging_buckets_are_not_treated_as_entities():
    ins = insights_of(P.answer("Phân tích tuổi nợ Đại lý cấp 2"))
    acts = " ".join(i["text"] for i in ins["sections"][3]["items"])
    assert "của Quá hạn" not in acts and "Đại lý cấp 2" in acts


def test_insights_can_be_disabled():
    p2 = Pipeline(dataclasses.replace(load_settings(), insights_enabled=False))
    assert not any(b["type"] == "insights" for b in p2.answer("Doanh thu theo chi nhánh")["blocks"])


def test_catalog_rejects_inverted_thresholds(tmp_path):
    import shutil

    from app.engine.catalog import MODELS_DIR, Catalog
    d = tmp_path / "models"
    shutil.copytree(MODELS_DIR, d)
    f = d / "sales.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("assess: {good: 0.21, warn: 0.19}",
                                                       "assess: {good: 0.15, warn: 0.19}"), encoding="utf-8")
    with pytest.raises(ValueError):
        Catalog(P.db, d)


# ============================================================ Dashboard
DASH_CASES = [(mode, m) for mode in ("mtd", "qtd", "ytd") for m in (1, 3, 8, 9, 12)]


def _asks(d):
    out = [k["ask"] for k in d["kpis"]] + [b["ask"] for b in d["branches"]]
    out += [d["trend"]["ask"], d["groups_ask"], d["top_customers_ask"], d["top_products_ask"]]
    return out


def test_dashboard_q3_matches_independent_sql():
    s = story()
    d = P.dashboard.build(2025, 9, "qtd")
    k = {x["key"]: x for x in d["kpis"]}
    assert k["doanh_thu_thuan"]["value"] == s["rev"][3]
    assert abs(k["doanh_thu_thuan"]["change"] - s["rev_g"]) < 1e-9
    assert abs(k["loi_nhuan_thuan"]["change"] - s["np_g"]) < 1e-9
    assert abs(k["thuc_thu"]["change"] - s["cash_g"]) < 1e-9
    assert abs(k["doanh_thu_thuan"]["extra"][1]["value"] - s["kpi_q3"]) < 1e-9
    assert sum(b["actual"] for b in d["branches"]) == s["rev"][3]
    assert abs(sum(b["share"] for b in d["branches"]) - 1) < 1e-9
    south = next(b for b in d["branches"] if b["name"] == "Miền Nam")
    assert south["status"]["code"] == "good"


@pytest.mark.parametrize("mode,m", DASH_CASES)
def test_dashboard_every_ask_is_answerable(mode, m):
    d = P.dashboard.build(2025, m, mode)
    asks = _asks(d)
    assert all(asks), "mỗi thẻ phải có câu Hỏi AI"
    for q in set(asks):
        assert P.answer(q)["mode"] == "descriptive", q


def test_dashboard_numbers_equal_chatbot_answer():
    d = P.dashboard.build(2025, 9, "qtd")
    ask = d["kpis"][0]["ask"]                      # Doanh thu thuần theo chi nhánh quý 3 so với quý trước
    r = run(ask)
    assert r.totals["doanh_thu_thuan"] == d["kpis"][0]["value"]
    by = {x["chi_nhanh"]: x["doanh_thu_thuan"] for x in r.rows}
    assert all(by[b["name"]] == b["actual"] for b in d["branches"])


def test_dashboard_kpi_prorated_and_periods():
    q3 = P.dashboard.build(2025, 9, "qtd")
    m7 = P.dashboard.build(2025, 7, "mtd")
    kq3 = q3["kpis"][0]["extra"][0]["value"]
    assert abs(m7["kpis"][0]["extra"][0]["value"] - kq3 / 3) < 1        # KPI tháng = KPI quý / 3
    assert m7["period"]["kpi_note"] and q3["period"]["kpi_note"] is None
    y = P.dashboard.build(2025, 12, "ytd")
    assert y["period"]["prior_label"] is None and y["kpis"][0]["change"] is None
    assert P.dashboard.build(2025, 1, "mtd")["period"]["prior_label"] is None


def test_dashboard_api_and_pages():
    assert client.get("/api/v1/dashboard/meta").json()["default_month"] == 12
    assert client.get("/api/v1/dashboard?mode=qtd&month=9").status_code == 200
    assert client.get("/api/v1/dashboard?mode=abc").status_code == 400
    assert client.get("/api/v1/dashboard?mode=qtd&month=13").status_code == 400
    assert "Revenue Dashboard" in client.get("/").text
    assert "Trợ lý Phân tích" in client.get("/chat").text


# ============================================================ LLM miễn phí
def _prov(name):
    return LLMProvider(name=name, label=name, base_url="http://x", model=f"{name}-m", api_key="k",
                       reasoning_effort="low", json_mode=False, timeout=1)


def _router(behaviour):
    r = LLMRouter([_prov("gemini"), _prov("groq")], P.catalog, 2025, "2025-12-31")
    calls = []

    def fake_call(p, q):
        calls.append(p.name)
        out = behaviour(p.name, q)
        if isinstance(out, Exception):
            raise out
        return out
    r._call = fake_call
    return r, calls


def _http(code, headers=None):
    req = httpx.Request("POST", "http://x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, headers=headers or {}, request=req))


def test_llm_fallback_cooldown_and_cache():
    intent = {"type": "query", "metrics": ["doanh_thu_thuan"]}
    r, calls = _router(lambda n, q: _http(429, {"retry-after": "30"}) if n == "gemini" else intent)
    assert r.parse("doanh thu")[1] == "llm:groq/groq-m"
    assert r.parse("doanh thu")[1].endswith("(cache)")
    r.parse("cau khac")            # gemini đang nghỉ -> gọi thẳng groq
    assert calls == ["gemini", "groq", "groq"]
    assert r.status()[0]["cooldown_s"] > 0


def test_llm_all_fail_falls_back_to_rules():
    r, _ = _router(lambda n, q: httpx.ReadTimeout("t"))
    with pytest.raises(LLMError):
        r.parse("doanh thu")
    P.llm, old = r, P.llm
    try:
        res = P.answer("Doanh thu theo chi nhánh quý 3")
        assert res["mode"] == "descriptive" and res["router"].startswith("rules")
    finally:
        P.llm = old


def test_llm_invalid_intent_is_rejected_then_rules():
    r, _ = _router(lambda n, q: {"type": "query", "metrics": ["ebitda_bia"]})
    P.llm, old = r, P.llm
    try:
        res = P.answer("Lợi nhuận thuần theo quý")
        assert res["mode"] == "descriptive" and "không hợp lệ" in res["router"]
    finally:
        P.llm = old


def test_extract_json_handles_think_and_fences():
    assert extract_json('<think>a{b}</think>```json\n{"metrics":["x"]}\n```')["metrics"] == ["x"]

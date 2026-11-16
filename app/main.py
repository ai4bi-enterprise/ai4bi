"""
AI4BI Chatbot – Community Edition
Một tiến trình FastAPI phục vụ cả API (/api/v1/*) lẫn WebUI đã build sẵn (webui_dist/).
Chạy: python -m uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, load_settings
from .engine.pipeline import MORE_PROMPTS, QUICK_PROMPTS, Pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("ai4bi")

VERSION = "1.2.0"
settings = load_settings()
pipeline = Pipeline(settings)
app = FastAPI(title="AI4BI Community Edition", version=VERSION)
log.info("AI4BI %s | DB=%s | Router=%s | Insights=%s", VERSION, settings.db_path.name,
         "LLM " + " -> ".join(p.tag for p in settings.llm_providers) + " -> rules" if pipeline.llm
         else "Rule-based (không cần API key)", "bật" if settings.insights_enabled else "tắt")


class ChatIn(BaseModel):
    question: str = Field(..., max_length=500)
    bod_mode: bool = False


# --- Giới hạn số câu hỏi/IP/ngày cho Live Demo công khai (0 = tắt)
_usage: dict[tuple[str, str], int] = defaultdict(int)


def _check_rate(request: Request) -> None:
    limit = settings.rate_limit_per_day
    if limit <= 0:
        return
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    key = (ip, date.today().isoformat())
    if _usage[key] >= limit:
        raise HTTPException(429, f"Bản Live Demo giới hạn {limit} câu hỏi/ngày. Hãy tải repo về để dùng không giới hạn.")
    _usage[key] += 1


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": VERSION, "router": "llm" if pipeline.llm else "rules",
            "llm": pipeline.llm.status() if pipeline.llm else [],
            "insights": settings.insights_enabled,
            "data_end": pipeline.meta.get("ngay_chot_du_lieu"), "company": pipeline.meta.get("ten_cong_ty")}


@app.get("/api/v1/catalog")
def catalog():
    return pipeline.catalog.public()


@app.get("/api/v1/suggestions")
def suggestions():
    return {"quick": QUICK_PROMPTS, "more": MORE_PROMPTS}


@app.get("/api/v1/dashboard/meta")
def dashboard_meta():
    return pipeline.dashboard.meta_info()


@app.get("/api/v1/dashboard")
def dashboard(mode: str = "qtd", month: int | None = None, year: int | None = None):
    try:
        return pipeline.dashboard.build(year, month, mode)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/v1/chat")
def chat(payload: ChatIn, request: Request):
    _check_rate(request)
    t = time.perf_counter()
    try:
        res = pipeline.answer(payload.question, payload.bod_mode)
    except Exception:  # không lộ stacktrace ra ngoài
        log.exception("Lỗi xử lý câu hỏi")
        return JSONResponse(status_code=500, content={"mode": "error", "blocks": [
            {"type": "text", "md": "Xin lỗi, đã có lỗi khi xử lý câu hỏi. Vui lòng thử lại."}], "sql": []})
    log.info("Q=%r | mode=%s | router=%s | %.0f ms", payload.question[:80], res["mode"], res["router"],
             (time.perf_counter() - t) * 1000)
    return res


# --- WebUI tĩnh (đặt cuối file để không che các route /api)
WEBUI = ROOT / "webui_dist"
if WEBUI.exists():
    if (WEBUI / "assets").exists():
        app.mount("/assets", StaticFiles(directory=WEBUI / "assets"), name="assets")

    PAGES = {"": "dashboard.html", "dashboard": "dashboard.html", "chat": "chat.html"}

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_webui(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        page = PAGES.get(full_path.strip("/"))
        if page:
            return FileResponse(WEBUI / page)
        f = (WEBUI / full_path).resolve()
        if f.is_file() and WEBUI.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(WEBUI / "dashboard.html")

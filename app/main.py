"""AI4BI – AI Mỗi Ngày: API dashboard + chatbot và phục vụ giao diện web.

Chạy: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.chat import engine
from app.core import db
from app.core.periods import make
from app.core.scope import User, current_user, require, users
from app.core.semantic import SemanticError
from app.modules import MENU, REGISTRY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ai4bi")
STATIC = config.ROOT / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):  # Starlette 1.x: dùng lifespan, không dùng on_event (Blueprint bài học 12)
    db.meta()
    import threading

    def warm():  # làm nóng cache kỳ mặc định cho người dùng toàn quyền (Blueprint 8.5)
        y, m = (int(x) for x in db.meta()["last_closed_month"].split("-"))
        u = next((x for x in users().values() if x.is_all and "*" in x.modules), None)
        if u:
            for mod in REGISTRY:
                try:
                    dashboard(mod, y, m, "quarter", None, None, u)
                except Exception as e:  # noqa: BLE001
                    log.warning("Làm nóng %s lỗi: %s", mod, e)
            log.info("Đã làm nóng cache %d phân hệ", len(REGISTRY))
    if not os.getenv("VERCEL"):  # serverless: instance ngắn hạn, bỏ làm nóng để không dồn truy vấn lúc cold start
        threading.Thread(target=warm, daemon=True).start()
    log.info("AI4BI %s sẵn sàng – %s – LLM: %s", config.EDITION.title(), db.meta().get("company_name"), "bật" if config.llm_on() else "tắt (luật)")
    yield


app = FastAPI(title=f"AI4BI {config.EDITION.title()} – AI Mỗi Ngày", version="2.0.0", lifespan=lifespan)


from app.core.cache import cache  # noqa: E402



SAFE = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def timing(request: Request, call_next):
    t = time.time()
    # Chống CSRF: request ghi từ trình duyệt phải cùng origin (cookie SameSite là lớp thứ hai)
    origin = request.headers.get("origin")
    if request.method not in SAFE and origin:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        if origin.split("://", 1)[-1] != host:
            return JSONResponse(status_code=403, content={"detail": "Yêu cầu không hợp lệ"})
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/login", "/admin/users"):
        resp.headers["Cache-Control"] = "no-cache"  # luôn kiểm ETag -> deploy giao diện mới không bị kẹt bản cũ
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # dữ liệu theo người dùng: không cho CDN/trình duyệt lưu
        log.info("%s %s %s %dms user=%s", request.method, request.url.path, resp.status_code,
                 (time.time() - t) * 1000, request.headers.get("x-demo-user", "-"))
    return resp


@app.exception_handler(SemanticError)
async def semantic_error(_: Request, e: SemanticError):
    return JSONResponse(status_code=403, content={"detail": str(e)})


@app.exception_handler(Exception)
async def any_error(_: Request, e: Exception):  # người dùng chỉ thấy câu chung; chi tiết vào log (Blueprint P12)
    log.exception("Lỗi xử lý: %s", e)
    return JSONResponse(status_code=500, content={"detail": "Hệ thống đang bận, vui lòng thử lại sau."})


# ------------------------------------------------------------------ API
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ai4bi", "edition": config.EDITION, "llm": config.llm_on()}


@app.get("/api/meta")
def meta(user: User = Depends(current_user)):
    m = db.meta()
    ly, lm = (int(x) for x in m["last_closed_month"].split("-"))
    first = db.fetch("SELECT MIN(year*100+month) AS v FROM bi_sales")[0]["v"]
    orgs = db.fetch("SELECT org_code, org_name, region_name FROM bi_org ORDER BY sort_order")
    if not user.is_all:
        orgs = [o for o in orgs if o["org_code"] in (user.orgs or set())]
    tree: dict[str, list[str]] = {}
    for o in orgs:
        tree.setdefault(o["region_name"], []).append(o["org_name"])
    menu = [{"group": g, "items": [{"key": k, "label": lab, "icon": ic, "org_filter": of,
                                    "locked": k in config.LOCKED_MODULES}
                                   for k, lab, ic, of in items if user.can(k) or k in config.LOCKED_MODULES]}
            for g, items in MENU]
    return {
        "company": m.get("company_name"), "as_of": m.get("as_of_date"), "latest": {"year": ly, "month": lm},
        "first": {"year": first // 100, "month": first % 100}, "source": m.get("source"), "note": m.get("transform_note"),
        "menu": [g for g in menu if g["items"]], "org_tree": tree, "can_chat": user.can("chat"), "llm": config.llm_on(),
        "user": {"username": user.username, "display_name": user.display_name, "role": user.role,
                 "scope": "Toàn bộ" if user.is_all else ", ".join(o["org_name"] for o in orgs) or "Không có"},
        "demo_users": [{"username": u.username, "display_name": u.display_name, "role": u.role} for u in users().values()]
        if config.AUTH_MODE == "demo" and config.PRO else [],
        "edition": {"name": config.EDITION, "upgrade_url": config.UPGRADE_URL, "contact_url": config.CONTACT_URL},
        "auth": {"mode": config.AUTH_MODE, "email": user.email, "is_admin": user.is_admin,
                 "perm_version": user.perm_version},
    }


if config.PRO:  # đăng nhập, quản trị người dùng, cấu hình LLM trên web – chỉ bản Pro
    from app.auth_api import admin_router, router as auth_router
    from app.core import auth

    @app.exception_handler(auth.AuthError)
    async def auth_error(_: Request, e: auth.AuthError):
        return JSONResponse(status_code=e.status, content={"detail": e.detail})

    app.include_router(auth_router)
    app.include_router(admin_router)


@app.get("/api/dashboard/{module}")
def dashboard(module: str, year: int, month: int, mode: str = "month", region: str | None = None,
              org: str | None = None, user: User = Depends(current_user)):
    if module in config.LOCKED_MODULES:
        raise HTTPException(402, "Phân hệ này thuộc bản AI4BI Pro")
    if module not in REGISTRY:
        raise HTTPException(404, "Không có phân hệ này")
    require(user, module)
    if mode not in ("month", "quarter", "ytd") or not (1 <= month <= 12):
        raise HTTPException(400, "Kỳ không hợp lệ")
    filters = {k: v.split("|") for k, v in (("region", region), ("org", org)) if v}
    scope_key = "*" if user.is_all else ",".join(sorted(user.orgs or []))
    period = make(year, month, mode)
    key = ("page", module, period, repr(sorted(filters.items())), scope_key, tuple(user.modules))  # cùng khoá với chatbot
    return cache.get(key, lambda: REGISTRY[module].build(period, user, filters))


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    context: dict = Field(default_factory=dict)
    history: list[str] = Field(default_factory=list)
    last: dict | None = None


@app.post("/api/chat/ask")
def ask(body: AskBody, user: User = Depends(current_user)):
    require(user, "chat")
    res = engine.answer(body.question, body.context, user, body.history[-4:], body.last)
    log.info("CHAT user=%s parser=%s lane=%s %dms q=%r", user.username, res.get("parser"), res.get("lane"),
             res.get("elapsed_ms", 0), body.question[:120])
    return res


@app.get("/api/chat/welcome")
def welcome(user: User = Depends(current_user)):
    require(user, "chat")
    return engine.welcome(user)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if config.PRO:
    @app.get("/login")
    def login_page():
        return FileResponse(STATIC / "login.html")

    @app.get("/admin/users")
    def admin_page():
        return FileResponse(STATIC / "admin.html")

"""AI4BI – AI Mỗi Ngày: API dashboard + chatbot và phục vụ giao diện web.

Chạy: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.chat import engine
from app.core import dataset, db
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
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/login", "/admin/users", "/data", "/team/chat-log"):
        resp.headers["Cache-Control"] = "no-cache"  # luôn kiểm ETag -> deploy giao diện mới không bị kẹt bản cũ
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # dữ liệu theo người dùng: không cho CDN/trình duyệt lưu
        log.info("%s %s %s %dms user=%s", request.method, request.url.path, resp.status_code,
                 (time.time() - t) * 1000, request.headers.get("x-demo-user", "-"))
    return resp


@app.exception_handler(SemanticError)
async def semantic_error(_: Request, e: SemanticError):
    return JSONResponse(status_code=403, content={"detail": str(e)})


@app.exception_handler(dataset.UploadError)
async def upload_error(_: Request, e: dataset.UploadError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


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
        "chat_history": CHATLOG and not user.is_preview,
        "preview": user.is_preview, "can_export": not user.is_preview,   # [preview-v1]
        "announcements": CHATLOG,                       # thông báo hệ thống (bản Pro có đăng nhập)
        "team_log": CHATLOG and user.can("chat_log_team"),
        "user": {"username": user.username, "display_name": user.display_name, "role": user.role,
                 "scope": "Toàn bộ" if user.is_all else ", ".join(o["org_name"] for o in orgs) or "Không có"},
        "demo_users": [{"username": u.username, "display_name": u.display_name, "role": u.role} for u in users().values()]
        if config.AUTH_MODE == "demo" and config.PRO else [],
        "data": {"upload": dataset.enabled() and (config.AUTH_MODE != "password" or user.is_admin),
                 "active": dataset.active()},
        "edition": {"name": config.EDITION, "upgrade_url": config.UPGRADE_URL, "demo_url": config.DEMO_URL,
                    "contact": config.CONTACT},
        "auth": {"mode": config.AUTH_MODE, "email": user.email, "is_admin": user.is_admin,
                 "perm_version": user.perm_version},
    }


if config.PRO:  # đăng nhập, quản trị người dùng, cấu hình LLM trên web – chỉ bản Pro
    from app.auth_api import admin_router, router as auth_router, user_router
    from app.core import auth

    @app.exception_handler(auth.AuthError)
    async def auth_error(_: Request, e: auth.AuthError):
        return JSONResponse(status_code=e.status, content={"detail": e.detail})

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(user_router)


@app.get("/api/dashboard/{module}")
def dashboard(module: str, year: int, month: int, mode: str = "month", region: str | None = None,
              org: str | None = None, user: User = Depends(current_user)):
    if module in config.LOCKED_MODULES:
        raise HTTPException(402, "Phân hệ này thuộc bản AI4BI Pro")
    if module not in REGISTRY:
        raise HTTPException(404, "Không có phân hệ này")
    if not dataset.has_data(module):
        raise HTTPException(404, "Chưa có dữ liệu cho phân hệ này – nạp thêm ở trang Dữ liệu của tôi")
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
    session_id: str | None = Field(default=None, max_length=36)


CHATLOG = config.PRO and config.AUTH_MODE == "password"   # nhật ký + hạn mức cần CSDL sec (bản Pro, có đăng nhập)


class FeedbackBody(BaseModel):
    log_id: int
    vote: int
    note: str = Field(default="", max_length=500)


@app.post("/api/chat/ask")
def ask(body: AskBody, request: Request, user: User = Depends(current_user)):
    require(user, "chat")
    from app.chat import llm
    if CHATLOG:
        from app.core import chatlog
        chatlog.check(user.user_id)          # khối ① Guard: hạn mức câu / token trong ngày
    meter, t0 = llm.start_meter(), time.time()
    try:
        res = engine.answer(body.question, body.context, user, body.history[-4:], body.last)
    except Exception:
        if CHATLOG:
            chatlog.record(user.user_id, engine.redact(body.question),
                           {"status": "error", "elapsed_ms": int((time.time() - t0) * 1000)}, meter)
        raise
    facts = res.pop("facts", None)           # evidence chỉ vào nhật ký, không gửi lại trình duyệt
    if CHATLOG:                              # khối ⑫ Audit
        res["log_id"] = chatlog.record(user.user_id, engine.redact(body.question), {**res, "facts": facts}, meter)
        if res.get("status") == "out_of_scope" and not user.is_admin:   # Blueprint 14.3: vi phạm -> vượt ngưỡng tự khoá
            from app.auth_api import client_ip
            from app.core import access
            v = access.violation(user.user_id, f"log_id={res['log_id']} q={engine.redact(body.question)[:200]}",
                                 client_ip(request))
            if v["locked"]:
                res["answer"] += ("\n\nTài khoản của bạn đã bị tạm khoá vì hỏi dữ liệu ngoài phạm vi "
                                  f"{v['count']} lần. Liên hệ quản trị để mở lại.")
                res["locked"] = True
            elif v["limit"]:
                res["answer"] += (f"\n\nLưu ý: đây là lần thứ {v['count']}/{v['limit']} hỏi ngoài phạm vi; "
                                  "đến ngưỡng tài khoản sẽ tự khoá.")
    if CHATLOG and not user.is_preview:      # lịch sử theo tài khoản (Blueprint 13.3); tài khoản chung không lưu
        from app.core import chathistory
        res["session_id"] = chathistory.append(user.user_id, body.session_id, engine.redact(body.question), res,
                                               user.perm_version)
    log.info("CHAT user=%s parser=%s lane=%s %dms q=%r", user.username, res.get("parser"), res.get("lane"),
             res.get("elapsed_ms", 0), engine.redact(body.question)[:120])
    return res


@app.post("/api/chat/feedback")
def chat_feedback(body: FeedbackBody, user: User = Depends(current_user)):
    if not CHATLOG:
        raise HTTPException(404, "Bản demo không lưu phản hồi")
    require(user, "chat")
    from app.core import chatlog
    chatlog.feedback(user.user_id, body.log_id, body.vote, body.note)
    return {"ok": True}


@app.get("/api/chat/welcome")
def welcome(user: User = Depends(current_user)):
    require(user, "chat")
    return engine.welcome(user)


# ------------------------------------------------------------------ lịch sử chat (Blueprint 10.3 chat_history)
class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def history_user(user: User = Depends(current_user)) -> User:
    if not CHATLOG or user.is_preview:
        raise HTTPException(404, "Bản này không lưu lịch sử chat")
    require(user, "chat")
    return user


@app.get("/api/chat/sessions")
def chat_sessions(user: User = Depends(history_user)):
    from app.core import chathistory
    return chathistory.sessions(user.user_id)


@app.get("/api/chat/sessions/{sid}")
def chat_session(sid: str, user: User = Depends(history_user)):
    from app.core import chathistory
    return chathistory.get(user.user_id, sid, user.perm_version)


@app.patch("/api/chat/sessions/{sid}")
def chat_session_rename(sid: str, body: RenameBody, user: User = Depends(history_user)):
    from app.core import chathistory
    chathistory.rename(user.user_id, sid, body.title)
    return {"ok": True}


@app.delete("/api/chat/sessions/{sid}")
def chat_session_delete(sid: str, user: User = Depends(history_user)):
    from app.core import chathistory
    chathistory.remove(user.user_id, sid)
    return {"ok": True}


@app.delete("/api/chat/sessions")
def chat_sessions_clear(user: User = Depends(history_user)):
    from app.core import chathistory
    return {"deleted": chathistory.remove(user.user_id)}


# ------------------------------------------------------------------ xuất Excel / chia sẻ HTML (Blueprint 13.3)
class ExportBody(BaseModel):
    question: str = Field(default="", max_length=500)
    answer: dict


class Turn(BaseModel):
    q: str = Field(default="", max_length=500)
    r: dict | None = None


class ShareBody(BaseModel):
    title: str = Field(default="Hỏi đáp dữ liệu", max_length=200)
    turns: list[Turn] = Field(min_length=1, max_length=40)


def _export_audit(user: User, request: Request, action: str, detail: str) -> None:
    """Số liệu rời hệ thống -> ghi audit (bản Pro có đăng nhập)."""
    if not CHATLOG:
        return
    from app.auth_api import client_ip
    from app.core import auth, secdb
    try:
        with secdb.engine().begin() as con:
            auth._audit(con, action, client_ip(request), actor=user.user_id, detail=detail[:300])
    except Exception as e:  # noqa: BLE001
        log.warning("Không ghi được audit %s: %s", action, e)


def export_user(user: User = Depends(current_user)) -> User:
    require(user, "chat")
    if user.is_preview:   # [preview-v1] xem được, tải về phải mua bản Pro
        raise HTTPException(403, "Tài khoản xem thử không tải được số liệu. Liên hệ để mua AI4BI Pro.")
    return user


def _download(content: bytes | str, name: str, mime: str) -> Response:
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                       "Cache-Control": "no-store"})


@app.post("/api/chat/export-xlsx")
def chat_export_xlsx(body: ExportBody, request: Request, user: User = Depends(export_user)):
    from app.core import export
    data = export.xlsx(body.question, body.answer, db.meta().get("company_name", ""), user.display_name)
    _export_audit(user, request, "chat_export", f"xlsx q={engine.redact(body.question)[:200]}")
    return _download(data, export.filename(body.question or "tra_loi", "xlsx"),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/chat/share-log")
def chat_share_log(body: ShareBody, request: Request, user: User = Depends(export_user)):
    from app.core import export
    page = export.share_html(body.title, [t.model_dump() for t in body.turns], db.meta().get("company_name", ""),
                             user.display_name)
    _export_audit(user, request, "chat_share", f"html {len(body.turns)} câu: {engine.redact(body.title)[:200]}")
    return _download(page.encode(), export.filename(body.title, "html"), "text/html; charset=utf-8")


# ------------------------------------------------------------------ Dữ liệu của tôi (app/core/dataset.py)
def data_user(user: User = Depends(current_user)) -> User:
    """Chỉ khi chạy trên máy; bản có đăng nhập thì chỉ quản trị viên được thay dữ liệu."""
    if not dataset.enabled():
        raise HTTPException(404, "Chức năng nạp dữ liệu chỉ có khi chạy AI4BI trên máy của bạn")
    if config.AUTH_MODE == "password" and not user.is_admin:
        raise HTTPException(403, "Chỉ quản trị viên được nạp dữ liệu")
    return user


@app.get("/api/data/status")
def data_status(_: User = Depends(data_user)):
    return {"active": dataset.active(), "mine": dataset.user_info(), "edition": config.EDITION,
            "modules": [m for m in dataset.MODULE_TABLE if dataset.has_data(m) and m not in config.LOCKED_MODULES],
            "sheets": [{"name": s.name, "title": s.title, "required": s.required,
                        "cols": [{"header": c.header, "required": c.required, "note": c.note} for c in s.cols]}
                       for s in dataset.sheets()]}


@app.get("/api/data/template")
def data_template(_: User = Depends(data_user)):
    name = f"AI4BI_Mau_Du_Lieu_{config.EDITION.title()}.xlsx"
    return Response(dataset.template(), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/data/sample")
def data_sample(_: User = Depends(data_user)):
    """File ví dụ đã điền sẵn (tools/make_sample_data.py) – xem cách điền hoặc nạp thử ngay."""
    path = config.ROOT / "data" / "samples" / "ChuoiCaPhe_ViDu.xlsx"
    if not path.exists():
        raise HTTPException(404, "Không có file ví dụ")
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/data/upload")
async def data_upload(request: Request, _: User = Depends(data_user)):
    body = await request.body()
    if not body:
        raise HTTPException(400, "File rỗng")
    name = (request.headers.get("x-file-name") or "du_lieu.xlsx")[:200]
    from urllib.parse import unquote
    rep, path = await run_in_threadpool(dataset.build, body, unquote(name))
    log.info("DATA upload %s: ok=%s, %d lỗi, %d cảnh báo", name, rep.ok, len(rep.errors), len(rep.warnings))
    return {**rep.as_dict(), "can_activate": path is not None}


@app.post("/api/data/activate")
def data_activate(_: User = Depends(data_user)):
    dataset.activate_staging()
    return {"ok": True, "active": dataset.active()}


@app.post("/api/data/use/{which}")
def data_use(which: str, _: User = Depends(data_user)):
    if which not in ("demo", "user"):
        raise HTTPException(404, "Không có bộ dữ liệu này")
    dataset.switch(which)
    return {"ok": True, "active": dataset.active()}


@app.delete("/api/data/mine")
def data_delete(_: User = Depends(data_user)):
    dataset.delete_user_data()
    return {"ok": True, "active": dataset.active()}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/data")
def data_page():
    return FileResponse(STATIC / "data.html")


if config.PRO:
    @app.get("/login")
    def login_page():
        return FileResponse(STATIC / "login.html")

    @app.get("/admin/users")
    def admin_page():
        return FileResponse(STATIC / "admin.html")

    @app.get("/team/chat-log")
    def team_page():
        return FileResponse(STATIC / "team.html")

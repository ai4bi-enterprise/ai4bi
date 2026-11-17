"""Hai điểm chạm LLM DUY NHẤT (Blueprint P4): (1) đọc hiểu câu -> phiếu; (2) viết lời nhận xét từ số đã tính.

LLM không viết SQL, không tạo số, không chọn phạm vi. Mọi đầu ra đều được code kiểm lại.
Nhà cung cấp (Blueprint mục 5, 12.4): Anthropic -> OpenAI -> Gemini, gọi HTTP API trực tiếp, thứ tự dự phòng cố định;
khoá + model từng vai trò (Router, chuyên gia) cấu hình trên trang quản trị (app/core/llmkeys.py).
Hãng lỗi / hết hạn mức / quá giờ -> thử hãng kế tiếp; hết hãng -> engine dùng bộ hiểu câu theo luật.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from app import config
from app.core.semantic import catalog, model


def keys():
    """Nguồn khoá: Pro = trang quản trị (+ .env); Lite = chỉ .env."""
    if config.PRO:
        from app.core import llmkeys
        return llmkeys
    from app.core import envkeys
    return envkeys


log = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_BETA = "server-side-fallback-2026-07-01"


class ProviderError(RuntimeError):
    """Lỗi từ một hãng; thông điệp đã làm sạch (không chứa khoá), hiển thị được trên trang quản trị."""


def _scrub(text, key: str | None) -> str:
    text = re.sub(r"\s+", " ", str(text))
    if key:
        text = text.replace(key, "***")
    return text[:240]


HTTP_HINT = {400: "Yêu cầu bị từ chối", 401: "Khoá không hợp lệ", 403: "Khoá không có quyền",
             404: "Model không tồn tại", 413: "Câu hỏi quá dài với model này",
             429: "Hết hạn mức / gọi quá nhanh (rate limit)"}


def _http_error(r: httpx.Response, key: str) -> ProviderError:
    try:
        detail = r.json()
    except ValueError:
        detail = r.text
    for _ in range(3):   # OpenAI/Anthropic: {"error": {"message"}}; Gemini: [{"error": {...}}]
        if isinstance(detail, list):
            detail = detail[0] if detail else ""
        elif isinstance(detail, dict):
            detail = detail.get("error") or detail.get("message") or detail
            if isinstance(detail, dict) and "message" in detail:
                detail = detail["message"]
        else:
            break
    head = HTTP_HINT.get(r.status_code, "Máy chủ hãng lỗi" if r.status_code >= 500 else "Lỗi")
    if r.status_code == 400 and "api key" in str(detail).lower():   # Gemini báo khoá sai bằng 400
        head = HTTP_HINT[401]
    return ProviderError(f"HTTP {r.status_code} – {head}: {_scrub(detail, key)}")


def _post(url: str, body: dict, headers: dict, key: str) -> dict:
    try:
        with httpx.Client(timeout=config.LLM_PROVIDER_TIMEOUT) as c:
            r = c.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise ProviderError(f"Quá {config.LLM_PROVIDER_TIMEOUT:.0f} giây không phản hồi") from None
    except httpx.HTTPError as e:
        raise ProviderError(f"Không kết nối được: {type(e).__name__}") from None
    if r.status_code >= 400:
        raise _http_error(r, key)
    try:
        return r.json()
    except ValueError:
        raise ProviderError("Phản hồi không phải JSON") from None


def _extract_json(text: str) -> str:
    """Bỏ ```json … ``` hoặc lời dẫn quanh JSON."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    if t.startswith("{"):
        return t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise ProviderError("Model không trả về JSON")
    return t[i:j + 1]


def _anthropic(p: dict, mdl: str, system: str, user: str, schema: dict | None, effort: str, max_tokens: int) -> str:
    body = {"model": mdl, "max_tokens": max_tokens, "system": system, "fallbacks": "default",
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})}}
    headers = {"x-api-key": p["api_key"], "anthropic-version": ANTHROPIC_VERSION, "anthropic-beta": ANTHROPIC_BETA}
    data = _post(p["base_url"] + "/messages", body, headers, p["api_key"])
    if data.get("stop_reason") == "refusal":
        raise ProviderError("LLM từ chối yêu cầu")
    text = next((b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), "")
    if not text.strip():
        raise ProviderError("Model trả về rỗng")
    return text


def _openai(p: dict, mdl: str, system: str, user: str, schema: dict | None, max_tokens: int) -> str:
    """OpenAI và Gemini (endpoint tương thích OpenAI của Google) dùng chung /chat/completions."""
    body = {"model": mdl, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
    url, headers = p["base_url"] + "/chat/completions", {"Authorization": f"Bearer {p['api_key']}"}
    try:
        data = _post(url, body, headers, p["api_key"])
    except ProviderError as e:
        if not schema or not str(e).startswith("HTTP 400 – Yêu cầu bị từ chối"):
            raise
        # model không nhận JSON Schema -> json_object + lược đồ trong lời dặn; code vẫn kiểm phiếu sau
        body["response_format"] = {"type": "json_object"}
        body["messages"][0]["content"] = (f"{system}\n\nCHỈ trả về MỘT đối tượng JSON đúng JSON Schema sau:\n"
                                          f"{json.dumps(schema, ensure_ascii=False)}")
        data = _post(url, body, headers, p["api_key"])
    try:
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        raise ProviderError("Phản hồi không đúng định dạng") from None
    if not text.strip():
        raise ProviderError(f"Model trả về rỗng (finish_reason={choice.get('finish_reason')})")
    return _extract_json(text) if schema else text


def call_one(p: dict, role: str, system: str, user: str, schema: dict | None, effort: str = "low",
             max_tokens: int = 3000) -> str:
    mdl = p["model_expert"] if role == "expert" else p["model_router"]
    if p["provider"] == "anthropic":
        return _anthropic(p, mdl, system, user, schema, effort, max_tokens)
    return _openai(p, mdl, system, user, schema, max_tokens)


def _call(system: str, user: str, schema: dict | None, effort: str, max_tokens: int, role: str = "router") -> str:
    errors = []
    for p in keys().chain():
        t = time.time()
        try:
            text = call_one(p, role, system, user, schema, effort, max_tokens)
            if schema:
                json.loads(text)   # JSON hỏng -> coi như hãng lỗi, thử hãng sau
            log.info("LLM %s: %s/%s %dms", role, p["provider"], p["model_expert" if role == "expert" else "model_router"],
                     (time.time() - t) * 1000)
            return text
        except (ProviderError, ValueError) as e:
            msg = str(e) if isinstance(e, ProviderError) else "Model trả JSON không hợp lệ"
            log.warning("LLM %s lỗi ở %s: %s – chuyển hãng kế tiếp", role, p["provider"], msg)
            errors.append(f"{p['label']}: {msg}")
    raise RuntimeError(("Không hãng LLM nào trả lời được: " + " | ".join(errors)) if errors else "Chưa có khoá LLM")


def test_key(p: dict) -> dict:
    """Nút "Kiểm tra" trên trang quản trị: Router phải trả đúng JSON; chuyên gia trả lời văn bản."""
    t = time.time()
    schema = {"type": "object", "additionalProperties": False, "required": ["ok"],
              "properties": {"ok": {"type": "boolean"}}}
    try:
        data = json.loads(call_one(p, "router", "Trả về JSON với ok=true.", "Kiểm tra kết nối.", schema, max_tokens=800))
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise ProviderError("Model trả JSON không đúng yêu cầu")
        call_one(p, "expert", "Trả lời ngắn bằng tiếng Việt.", "Nói 'xin chào'.", None, max_tokens=200)
        return {"ok": True, "ms": int((time.time() - t) * 1000),
                "message": f"Router ({p['model_router']}) và chuyên gia ({p['model_expert']}) đều chạy được"}
    except (ProviderError, ValueError) as e:
        msg = str(e) if isinstance(e, ProviderError) else "Model trả JSON không hợp lệ"
        return {"ok": False, "ms": int((time.time() - t) * 1000), "message": msg}


def list_models(p: dict) -> list[str]:
    """Danh sách model của hãng (GET /models) để chọn model cho từng vai trò."""
    key = p["api_key"]
    headers = ({"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION} if p["provider"] == "anthropic"
               else {"Authorization": f"Bearer {key}"})
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(p["base_url"] + "/models", headers=headers, params={"limit": 1000} if p["provider"] == "anthropic" else None)
    except httpx.HTTPError as e:
        raise ProviderError(f"Không kết nối được: {type(e).__name__}") from None
    if r.status_code >= 400:
        raise _http_error(r, key)
    try:
        data = r.json()
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        ids = [(m.get("id") or m.get("name") or "") if isinstance(m, dict) else str(m) for m in items]
    except (ValueError, AttributeError):
        raise ProviderError("Không đọc được danh sách model") from None
    return sorted({i.removeprefix("models/") for i in ids if i})[:500]


# ------------------------------------------------------------------ (1) đọc hiểu câu hỏi
def _qform_schema() -> dict:
    m = model()
    metric_keys = [k for k, v in m["metrics"].items() if not v.get("internal")]
    dim_keys = [k for k in m["dims"] if k != "month"]
    from app.chat.nlu import _blocks
    block_keys = sorted({b for _, b in _blocks()})
    part = {
        "type": "object", "additionalProperties": False,
        "required": ["intent", "metrics", "dims", "filters", "period", "compare", "top_n", "order", "block"],
        "properties": {
            "intent": {"type": "string", "enum": ["value", "breakdown", "rank", "trend", "explain", "block",
                                                   "greeting", "help", "unknown"]},
            "metrics": {"type": "array", "items": {"type": "string", "enum": metric_keys}},
            "dims": {"type": "array", "items": {"type": "string", "enum": dim_keys}},
            "filters": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["dim", "values"],
                "properties": {"dim": {"type": "string", "enum": list(catalog().keys())},
                               "values": {"type": "array", "items": {"type": "string"}}}}},
            "period": {"type": "object", "additionalProperties": False, "required": ["year", "month", "mode", "explicit"],
                       "properties": {"year": {"type": "integer"}, "month": {"type": "integer"},
                                      "mode": {"type": "string", "enum": ["month", "quarter", "ytd"]},
                                      "explicit": {"type": "boolean"}}},
            "compare": {"type": "array", "items": {"type": "string", "enum": ["yoy", "prev", "plan"]}},
            "top_n": {"type": ["integer", "null"]},
            "order": {"type": "string", "enum": ["desc", "asc"]},
            "block": {"type": ["string", "null"], "enum": [*block_keys, None]},
        },
    }
    return {"type": "object", "additionalProperties": False, "required": ["parts"],
            "properties": {"parts": {"type": "array", "items": part}}}


def _catalog_text() -> str:
    m = model()
    lines = ["CHỈ TIÊU (key: tên | cách gọi khác):"]
    for k, v in m["metrics"].items():
        if not v.get("internal"):
            lines.append(f"- {k}: {v['label']} | {', '.join(v.get('synonyms', [])[:5])}")
    lines.append("CHIỀU (key: tên):")
    lines += [f"- {k}: {v['label']}" for k, v in m["dims"].items() if k != "month"]
    lines.append("GIÁ TRỊ LỌC HỢP LỆ:")
    lines += [f"- {d}: {', '.join(vals)}" for d, vals in catalog().items()]
    from app.chat.nlu import _blocks
    lines.append("KHỐI DASHBOARD (block: từ khoá):")
    seen: dict[str, list[str]] = {}
    for kw, b in _blocks():
        seen.setdefault(b, []).append(kw)
    lines += [f"- {b}: {', '.join(kws[:4])}" for b, kws in seen.items()]
    return "\n".join(lines)


QFORM_SYSTEM = """Bạn là bộ phân tích câu hỏi của trợ lý BI. Nhiệm vụ DUY NHẤT: chuyển câu hỏi tiếng Việt thành phiếu JSON.
Không trả lời câu hỏi, không tính số. Chỉ dùng key có trong danh mục.
- Tách tối đa 3 ý (parts) nếu câu hỏi nhiều ý về chỉ tiêu/kỳ khác nhau; nhiều chỉ tiêu cùng kỳ thì gộp 1 ý.
- intent: value (hỏi con số), breakdown (theo chiều), rank (top/cao nhất/thấp nhất), trend (theo tháng/xu hướng),
  explain (vì sao/nguyên nhân), block (hỏi đúng một khối dashboard như cơ cấu tài sản, DuPont, chu kỳ tiền mặt, hàng chậm luân chuyển),
  greeting, help, unknown (ngoài phạm vi dữ liệu doanh nghiệp).
- period: nếu câu không nói kỳ thì dùng kỳ NGỮ CẢNH và explicit=false. "năm nay" = mode ytd đến tháng dữ liệu mới nhất;
  năm đã qua = ytd tháng 12; "quý N" = mode quarter, month = tháng cuối quý (không vượt tháng dữ liệu mới nhất).
- compare: yoy khi hỏi cùng kỳ/tăng trưởng; prev khi so kỳ trước; plan khi hỏi kế hoạch/KPI/% đạt.
- filters: chỉ dùng giá trị trong danh sách hợp lệ, viết đúng chính tả như danh sách.
- "đại lý nào/top đại lý" => dims [customer], filter channel Đại lý."""


def parse_qform(question: str, ctx: dict, history: list[str]) -> list[dict]:
    user = (f"{_catalog_text()}\n\nNGỮ CẢNH: kỳ đang xem {ctx.get('mode')} tháng {ctx.get('month')}/{ctx.get('year')}; "
            f"dữ liệu mới nhất tháng {ctx.get('latest_month')}/{ctx.get('latest_year')}.\n"
            f"CÂU TRƯỚC: {' | '.join(history[-2:]) or 'không có'}\n\nCÂU HỎI: {question}")
    data = json.loads(_call(QFORM_SYSTEM, user, _qform_schema(), effort="low", max_tokens=4000))
    parts = [p for p in (_coerce(x, ctx, question) for x in (data.get("parts") or [])[:3] if isinstance(x, dict)) if p]
    if not parts:
        raise ValueError("Phiếu LLM rỗng")
    return parts


INTENTS = {"value", "breakdown", "rank", "trend", "explain", "block", "greeting", "help", "unknown"}


def _strs(v) -> list[str]:
    v = v if isinstance(v, list) else [v] if v else []
    return [str(x) for x in v if isinstance(x, (str, int, float)) and str(x).strip()]


def _coerce(p: dict, ctx: dict, question: str) -> dict | None:
    """Ép phiếu về đúng kiểu trước khi engine.validate kiểm whitelist (model miễn phí không luôn theo lược đồ)."""
    intent = p.get("intent") if p.get("intent") in INTENTS else "unknown"
    raw = p.get("filters") or []
    if isinstance(raw, dict):   # {"region": ["Miền Bắc"]}
        raw = [{"dim": k, "values": v} for k, v in raw.items()]
    filters = {}
    for f in raw if isinstance(raw, list) else []:
        if isinstance(f, dict) and isinstance(f.get("dim"), str) and _strs(f.get("values")):
            filters[f["dim"]] = _strs(f.get("values"))
    per = p.get("period") if isinstance(p.get("period"), dict) else {}

    def num(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    period = {"year": num(per.get("year"), ctx.get("year") or ctx.get("latest_year")),
              "month": min(12, max(1, num(per.get("month"), ctx.get("month") or ctx.get("latest_month") or 12))),
              "mode": per.get("mode") if per.get("mode") in ("month", "quarter", "ytd") else ctx.get("mode", "month"),
              "explicit": per.get("explicit") is True}
    top = p.get("top_n")
    return {"intent": intent, "metrics": _strs(p.get("metrics")), "dims": _strs(p.get("dims")), "filters": filters,
            "period": period, "compare": [c for c in _strs(p.get("compare")) if c in ("yoy", "prev", "plan")],
            "top_n": max(1, min(50, top)) if isinstance(top, int) and not isinstance(top, bool) else None,
            "order": "asc" if p.get("order") == "asc" else "desc",
            "block": p.get("block") if isinstance(p.get("block"), str) else None, "question": question}


# ------------------------------------------------------------------ (2) viết lời chuyên gia
NARRATIVE_SYSTEM = """Bạn là chuyên gia phân tích tài chính viết cho Ban điều hành {company}.
Chỉ được dùng các con số có trong EVIDENCE, chép NGUYÊN VĂN cách viết số (VD "300,4 tỷ", "+12,5%").
Không tự tính số mới, không nêu nguyên nhân không có trong EVIDENCE; nếu là suy đoán thì ghi rõ "(giả thuyết)".
Viết tiếng Việt, ngắn gọn, tối đa 170 chữ, theo 3 mục markdown:
**Kết luận** – 1–2 câu trả lời thẳng câu hỏi.
**Phân tích** – 2–3 gạch đầu dòng về động lực chính, đánh giá tốt/xấu.
**Đề xuất** – 1–2 hành động cụ thể cho quản lý."""


def narrative(company: str, question: str, evidence: list[dict]) -> str:
    user = f"CÂU HỎI: {question}\n\nEVIDENCE (JSON):\n{json.dumps(evidence, ensure_ascii=False)}"
    return _call(NARRATIVE_SYSTEM.format(company=company), user, None, effort="low", max_tokens=3000,
                 role="expert").strip()

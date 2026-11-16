"""
LLM Router (tùy chọn) – chuỗi nhà cung cấp miễn phí: Gemini -> Groq -> ... -> Rule Router.

LLM CHỈ trả về Intent JSON (tên chỉ số/chiều/giá trị lọc). LLM không viết SQL và
không viết con số. Intent được Compiler kiểm duyệt lại với Catalog trước khi chạy.

Thiết kế cho free tier:
  - Fallback theo thứ tự LLM_PROVIDERS; lỗi/timeout/429 -> chuyển provider kế tiếp.
  - 429: tạm "nghỉ" provider theo header Retry-After (mặc định 60s) để không tốn độ trễ.
  - Cache intent theo câu hỏi đã chuẩn hóa (LRU) -> câu hỏi lặp lại không tốn quota.
  - Prompt gọn (~1K token) để vừa giới hạn TPM của Groq free plan.
  - 400 do tham số tùy chọn (reasoning_effort / response_format) -> tự thử lại bản tối giản.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict

import httpx

from ..config import LLMProvider
from .catalog import Catalog
from .text import normalize

log = logging.getLogger("ai4bi")

SYSTEM_PROMPT = """Bạn là ROUTER của chatbot BI. Chỉ chuyển câu hỏi tiếng Việt thành MỘT JSON intent.
KHÔNG trả lời câu hỏi, KHÔNG viết SQL, KHÔNG viết số liệu, KHÔNG giải thích.

{catalog}

Năm mặc định: {year}. "Hiện tại" = ngày chốt {asof}.

Schema (chỉ trả JSON, không markdown):
{{"type":"query|bod|chitchat","metrics":[],"dimensions":[],"filters":{{}},"year":null,"quarters":[],"months":[],"compare":null,"order":null,"limit":null}}

Quy tắc:
- metrics/dimensions/filters CHỈ dùng key trong danh mục; giá trị lọc viết đúng như danh mục.
- "theo X", "từng X", "các X" -> X vào dimensions. Nhắc tên một giá trị cụ thể -> filters.
- "đại lý" chung chung -> filters loai_khach_hang ["Đại lý cấp 1","Đại lý cấp 2"].
- "so với quý/tháng trước", "tăng trưởng", "biến động" -> compare "prev".
- "top N", "cao nhất" -> order "desc" (+ limit N); "thấp nhất" -> order "asc".
- "tuổi nợ" -> metrics ["du_no"], dimensions ["nhom_tuoi_no"].
- type "bod" khi hỏi TẠI SAO / nguyên nhân / bào mòn / cảnh báo. type "chitchat" khi không hỏi số liệu.
- Không chắc chỉ số -> "metrics": [].

Ví dụ:
Q: Doanh thu và biên lãi gộp từng chi nhánh quý 3
{{"type":"query","metrics":["doanh_thu_thuan","bien_loi_nhuan_gop"],"dimensions":["chi_nhanh"],"filters":{{}},"year":null,"quarters":[3],"months":[],"compare":null,"order":null,"limit":null}}
Q: 5 đại lý nợ quá hạn lớn nhất
{{"type":"query","metrics":["no_qua_han"],"dimensions":["khach_hang"],"filters":{{"loai_khach_hang":["Đại lý cấp 1","Đại lý cấp 2"]}},"year":null,"quarters":[],"months":[],"compare":null,"order":"desc","limit":5}}
Q: Tiền thu về của miền Nam tháng 8 so với tháng trước
{{"type":"query","metrics":["thuc_thu"],"dimensions":[],"filters":{{"chi_nhanh":["Miền Nam"]}},"year":null,"quarters":[],"months":[8],"compare":"prev","order":null,"limit":null}}
"""


def compact_catalog(cat: Catalog) -> str:
    """Gom chỉ số theo tập chiều giống nhau để prompt ngắn (tiết kiệm token free tier)."""
    groups: "OrderedDict[tuple, list[str]]" = OrderedDict()
    for m in cat.metrics.values():
        dims = tuple(sorted(d for d in cat.supported_dims(m.key) if d != "nam"))
        groups.setdefault(dims, []).append(f"{m.key} ({m.label})")
    lines = ["CHỈ SỐ (metrics) – nhóm theo chiều hỗ trợ:"]
    for dims, ms in groups.items():
        lines.append(f"* chiều [{', '.join(dims)}]: " + "; ".join(ms))
    lines.append("CHIỀU (dimensions):")
    for d in cat.dimensions.values():
        if d.key == "nam":
            continue
        vals = ""
        if d.kind != "time" and d.values and len(d.values) <= 12:
            vals = ": " + " | ".join(d.values)
        lines.append(f"* {d.key} ({d.label}){vals}")
    return "\n".join(lines)


class LLMError(Exception):
    pass


class LLMRouter:
    CACHE_SIZE = 256

    def __init__(self, providers: list[LLMProvider], catalog: Catalog, year: int, asof: str):
        if not providers:
            raise ValueError("Không có provider LLM nào được cấu hình")
        self.providers = providers
        self.prompt = SYSTEM_PROMPT.format(catalog=compact_catalog(catalog), year=year, asof=asof)
        self._cooldown: dict[str, float] = {}
        self._cache: "OrderedDict[str, tuple[dict, str]]" = OrderedDict()
        self._lock = threading.Lock()
        self.stats: dict[str, dict] = {p.name: {"ok": 0, "fail": 0, "rate_limited": 0} for p in providers}

    # ----------------------------------------------------------- public
    def status(self) -> list[dict]:
        now = time.time()
        return [{"provider": p.name, "label": p.label, "model": p.model,
                 "cooldown_s": max(0, round(self._cooldown.get(p.name, 0) - now)), **self.stats[p.name]}
                for p in self.providers]

    def parse(self, question: str) -> tuple[dict, str]:
        """Trả về (intent, tag provider). Ném LLMError nếu mọi provider đều lỗi."""
        key = normalize(question)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                intent, tag = self._cache[key]
                return json.loads(json.dumps(intent)), f"{tag} (cache)"
        errors = []
        for p in self.providers:
            wait = self._cooldown.get(p.name, 0) - time.time()
            if wait > 0:
                errors.append(f"{p.name}: đang nghỉ {wait:.0f}s do giới hạn free tier")
                continue
            try:
                t0 = time.perf_counter()
                intent = self._call(p, question)
                self.stats[p.name]["ok"] += 1
                tag = f"llm:{p.tag}"
                log.info("LLM %s OK %.0f ms", p.tag, (time.perf_counter() - t0) * 1000)
                with self._lock:
                    self._cache[key] = (intent, tag)
                    if len(self._cache) > self.CACHE_SIZE:
                        self._cache.popitem(last=False)
                return json.loads(json.dumps(intent)), tag
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 429:
                    self.stats[p.name]["rate_limited"] += 1
                    self._cooldown[p.name] = time.time() + _retry_after(e.response)
                else:
                    self.stats[p.name]["fail"] += 1
                errors.append(f"{p.name}: HTTP {code} {_short(e.response.text)}")
            except Exception as e:  # timeout, JSON hỏng, mạng...
                self.stats[p.name]["fail"] += 1
                errors.append(f"{p.name}: {type(e).__name__} {_short(str(e))}")
        raise LLMError(" | ".join(errors))

    # ---------------------------------------------------------- internal
    def _body(self, p: LLMProvider, question: str, minimal: bool) -> dict:
        body = {"model": p.model, "temperature": 0, "max_tokens": p.max_tokens,
                "messages": [{"role": "system", "content": self.prompt},
                             {"role": "user", "content": question}]}
        if not minimal:
            if p.reasoning_effort:
                body["reasoning_effort"] = p.reasoning_effort
            if p.json_mode:
                body["response_format"] = {"type": "json_object"}
            if p.name == "groq" and p.reasoning_effort:
                body["include_reasoning"] = False  # không trả chuỗi suy luận về -> nhẹ & JSON sạch
        return body

    def _call(self, p: LLMProvider, question: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if p.api_key:
            headers["Authorization"] = f"Bearer {p.api_key}"
        url = f"{p.base_url}/chat/completions"
        with httpx.Client(timeout=p.timeout) as client:
            resp = client.post(url, headers=headers, json=self._body(p, question, minimal=False))
            if resp.status_code == 400:
                # Tham số tùy chọn không được model hỗ trợ -> thử lại bản tối giản một lần
                log.warning("LLM %s trả 400 (%s) – thử lại không kèm tham số tùy chọn", p.tag, _short(resp.text))
                resp = client.post(url, headers=headers, json=self._body(p, question, minimal=True))
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
        return extract_json(msg.get("content") or "")


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(5.0, min(float(resp.headers.get("retry-after", "60")), 3600.0))
    except ValueError:
        return 60.0


def _short(s: str, n: int = 160) -> str:
    return re.sub(r"\s+", " ", s or "")[:n]


def extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM không trả về JSON")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON intent không hợp lệ")
    return data

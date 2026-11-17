"""Khoá LLM từ biến môi trường (.env) – dùng chung cho bản Lite và Pro (Blueprint mục 5).

Thứ tự dự phòng CỐ ĐỊNH: Anthropic -> OpenAI -> Gemini. Bản Pro có thêm trang quản trị (app/core/llmkeys.py)
để nhập khoá mã hoá; khoá ở đây chỉ dùng khi hãng đó chưa nhập khoá trên trang quản trị.
"""
from __future__ import annotations

from app import config

# Thứ tự dict = thứ tự dự phòng. Model mặc định đổi được trên trang quản trị (Pro).
PROVIDERS: dict[str, dict] = {
    "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com/v1", "env": "ANTHROPIC_API_KEY",
                  "router": config.LLM_MODEL, "expert": config.LLM_MODEL,
                  "signup": "https://console.anthropic.com/settings/keys"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "env": "OPENAI_API_KEY",
               "router": "gpt-4.1-mini", "expert": "gpt-4.1-mini", "signup": "https://platform.openai.com/api-keys"},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "env": "GEMINI_API_KEY", "router": "gemini-flash-latest", "expert": "gemini-flash-latest",
               "signup": "https://aistudio.google.com/apikey"},
}


def entry(key: str, api_key: str, source: str, model_router: str | None = None, model_expert: str | None = None) -> dict:
    prov = PROVIDERS[key]
    return {"provider": key, "label": prov["label"], "base_url": prov["base_url"], "api_key": api_key,
            "source": source, "model_router": model_router or prov["router"], "model_expert": model_expert or prov["expert"]}


def chain() -> list[dict]:
    """Các hãng có khoá trong biến môi trường, theo thứ tự dự phòng."""
    return [entry(k, getattr(config, p["env"]), "env") for k, p in PROVIDERS.items() if getattr(config, p["env"])]


def has_active() -> bool:
    return bool(chain())

"""Cấu hình đọc từ biến môi trường / file .env (không hard-code bí mật trong code)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------------------
# Preset nhà cung cấp LLM (API tương thích OpenAI /chat/completions).
# Đã đối chiếu tài liệu chính thức (11/2026):
#   - Gemini: base .../v1beta/openai; reasoning_effort ánh xạ sang thinking_level.
#     Gemini 3.x KHÔNG tắt được thinking -> dùng "low" (mức thấp nhất được mọi dòng 3.x hỗ trợ).
#   - Groq: base api.groq.com/openai/v1; gpt-oss-20b/120b nhận reasoning_effort low|medium|high,
#     hỗ trợ JSON Object Mode. Free plan ~30 RPM, 1K RPD, 8K TPM / tổ chức.
# Model ID thực tế luôn phải kiểm tra bằng: python tools/check_llm.py  (gọi /models của chính provider)
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict] = {
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-3.8-flash", "key_env": "GEMINI_API_KEY", "reasoning_effort": "low",
               "json_mode": False, "max_tokens": 2048, "free": True},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "model": "openai/gpt-oss-20b", "key_env": "GROQ_API_KEY", "reasoning_effort": "low",
             "json_mode": True, "max_tokens": 1024, "free": True},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model": "", "key_env": "OPENROUTER_API_KEY", "reasoning_effort": "", "json_mode": False,
                   "free": True},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "model": "gpt-4o-mini", "key_env": "OPENAI_API_KEY", "reasoning_effort": "", "json_mode": True,
               "free": False},
    "ollama": {"label": "Ollama (local)", "base_url": "http://localhost:11434/v1",
               "model": "", "key_env": "", "reasoning_effort": "", "json_mode": False, "free": True},
}


@dataclass(frozen=True)
class LLMProvider:
    name: str
    label: str
    base_url: str
    model: str
    api_key: str
    reasoning_effort: str      # "" = không gửi tham số
    json_mode: bool
    timeout: float
    max_tokens: int = 2048

    @property
    def tag(self) -> str:
        return f"{self.name}/{self.model}"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _bool(v: str, default: bool) -> bool:
    return default if v == "" else v.lower() in ("1", "true", "yes", "on")


def _build_provider(name: str, timeout: float, legacy_key: str = "", legacy_model: str = "",
                    legacy_base: str = "") -> LLMProvider | None:
    preset = PROVIDER_PRESETS.get(name)
    up = name.upper()
    if preset is None:
        # Nhà cung cấp tùy chỉnh: bắt buộc khai báo <NAME>_BASE_URL và <NAME>_MODEL
        preset = {"label": name, "base_url": "", "model": "", "key_env": f"{up}_API_KEY",
                  "reasoning_effort": "", "json_mode": False}
    key = _env(preset["key_env"]) if preset["key_env"] else ""
    key = key or legacy_key
    base = (_env(f"{up}_BASE_URL") or legacy_base or preset["base_url"]).rstrip("/")
    model = _env(f"{up}_MODEL") or legacy_model or preset["model"]
    if not base or not model:
        return None
    if preset["key_env"] and not key:
        return None  # không có key => bỏ qua provider này
    effort = _env(f"{up}_REASONING_EFFORT", preset["reasoning_effort"])
    effort = "" if effort.lower() in ("", "off", "default") else effort.lower()
    return LLMProvider(name=name, label=preset["label"], base_url=base, model=model, api_key=key,
                       reasoning_effort=effort, json_mode=_bool(_env(f"{up}_JSON_MODE"), preset["json_mode"]),
                       timeout=timeout, max_tokens=int(_env(f"{up}_MAX_TOKENS", str(preset.get("max_tokens", 2048)))))


def _db_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError("Community Edition chỉ hỗ trợ DATABASE_URL dạng sqlite:///<đường dẫn>")
    p = Path(url[len("sqlite:///"):])
    return p if p.is_absolute() else (ROOT / p).resolve()


@dataclass(frozen=True)
class Settings:
    llm_providers: list[LLMProvider] = field(default_factory=list)
    db_path: Path = ROOT / "sample_data" / "enterprise_sample.db"
    rate_limit_per_day: int = 0
    contact_url: str = "mailto:ai4bi.contact@gmail.com"
    insights_enabled: bool = True

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_providers)


def load_settings() -> Settings:
    timeout = float(_env("LLM_TIMEOUT", "15"))
    providers: list[LLMProvider] = []
    names = [n.strip().lower() for n in _env("LLM_PROVIDERS").split(",") if n.strip()]
    if names:
        for n in names:
            if n in ("none", "rules"):
                continue
            p = _build_provider(n, timeout)
            if p and all(x.name != p.name for x in providers):
                providers.append(p)
    else:
        # Tương thích ngược: LLM_PROVIDER + LLM_API_KEY + LLM_MODEL (+ LLM_BASE_URL)
        legacy = _env("LLM_PROVIDER", "none").lower()
        if legacy not in ("", "none", "rules"):
            p = _build_provider(legacy, timeout, _env("LLM_API_KEY"), _env("LLM_MODEL"), _env("LLM_BASE_URL"))
            if p:
                providers.append(p)
    return Settings(
        llm_providers=providers,
        db_path=_db_path(_env("DATABASE_URL", "sqlite:///./sample_data/enterprise_sample.db")),
        rate_limit_per_day=int(_env("DEMO_RATE_LIMIT_PER_DAY", "0")),
        contact_url=_env("CONTACT_URL", "mailto:ai4bi.contact@gmail.com"),
        insights_enabled=_bool(_env("INSIGHTS_ENABLED"), True),
    )

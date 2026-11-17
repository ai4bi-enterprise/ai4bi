"""Nơi DUY NHẤT đọc biến môi trường (Blueprint 10.2: env.py)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")  # do `neon init` / `neon env pull` sinh ra; .env thắng nếu trùng khoá

# Phiên bản: pro = repo private đầy đủ; lite = bản công khai (tools/export_lite.py lọc bỏ file Pro).
# Tự nhận theo việc có file Pro hay không; AI4BI_EDITION=lite để chạy thử bản Lite ngay trong repo Pro.
EDITION = os.getenv("AI4BI_EDITION", "").lower() or ("pro" if (ROOT / "app" / "core" / "auth.py").exists() else "lite")
if EDITION not in ("pro", "lite"):
    raise RuntimeError(f"AI4BI_EDITION không hợp lệ: {EDITION}")
PRO = EDITION == "pro"
PRO_MODULES = ("pnl", "bs", "cashflow", "inventory")  # phân hệ chỉ có ở bản Pro
LOCKED_MODULES = () if PRO else PRO_MODULES          # bản Lite: hiện trên menu kèm nhãn 🔒 PRO
UPGRADE_URL = os.getenv("UPGRADE_URL", "https://github.com/ai4bi-enterprise/ai4bi#-so-sánh-phiên-bản")
CONTACT_URL = os.getenv("CONTACT_URL", "https://calendly.com/ai4bi-contact/demo")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'aimoingay_demo.db').as_posix()}")
SEMANTIC_DIR = Path(os.getenv("SEMANTIC_DIR", ROOT / "app" / "semantic"))
USERS_FILE = Path(os.getenv("USERS_FILE", ROOT / "config" / "users.yaml"))
COMPANY_NAME = os.getenv("COMPANY_NAME", "")  # để trống = lấy từ bi_meta

# LLM (tuỳ chọn). Không có khoá -> chatbot dùng bộ hiểu câu theo luật, vẫn trả lời đúng số.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")     # Blueprint 5: Anthropic -> OpenAI -> Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")     # khoá nhập trên trang quản trị được ưu tiên hơn biến môi trường
LLM_ENABLED = os.getenv("LLM_ENABLED", "auto")  # auto | on | off
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
LLM_NARRATIVE = os.getenv("LLM_NARRATIVE", "true").lower() == "true"
# Khoá LLM nhập trên trang quản trị (Blueprint 14.5) được mã hoá bằng khoá này trước khi lưu vào sec.llm_providers.
# Blueprint dùng DPAPI (Windows); bản chạy Vercel/Linux dùng Fernet với secret riêng. Thiếu = không lưu được khoá.
LLM_KEY_SECRET = os.getenv("LLM_KEY_SECRET", "")
LLM_PROVIDER_TIMEOUT = float(os.getenv("LLM_PROVIDER_TIMEOUT", "20"))  # mỗi hãng; hết giờ -> hãng kế tiếp

SQL_ROW_LIMIT = int(os.getenv("SQL_ROW_LIMIT", "5000"))

# Đăng nhập (Blueprint 14.1). password = tài khoản + mật khẩu, JWT trong cookie httpOnly (mặc định).
# demo = chọn vai trò không mật khẩu, chỉ để trình diễn offline trên máy; bị từ chối khi chạy trên Vercel.
AUTH_MODE = os.getenv("AUTH_MODE", "password").lower() if PRO else "demo"  # Lite: 1 người dùng, không đăng nhập
SEC_DATABASE_URL = os.getenv("SEC_DATABASE_URL", "")    # login chỉ đọc/ghi schema sec
ACCESS_SECRET = os.getenv("ACCESS_SECRET", "")          # khoá ký access token
REFRESH_SECRET = os.getenv("REFRESH_SECRET", "")        # khoá ký refresh token (khác access)
ACCESS_HOURS = 8
REFRESH_DAYS = 30
LOCK_AFTER = 5            # sai mật khẩu liên tiếp
LOCK_MINUTES = 15
IP_FAIL_LIMIT = 20        # lần sai trên một IP trong LOCK_MINUTES
PASSWORD_MIN = 10


def check_auth_config() -> None:
    """Thiếu secret hoặc secret yếu thì dừng khởi động (Blueprint 14.1)."""
    if AUTH_MODE == "demo":
        if PRO and os.getenv("VERCEL"):  # bản Lite chỉ có dữ liệu mẫu nên được chạy Live Demo công khai
            raise RuntimeError("AUTH_MODE=demo không được dùng khi triển khai")
        return
    if AUTH_MODE != "password":
        raise RuntimeError(f"AUTH_MODE không hợp lệ: {AUTH_MODE}")
    weak = [n for n, v in (("ACCESS_SECRET", ACCESS_SECRET), ("REFRESH_SECRET", REFRESH_SECRET))
            if len(v) < 32 or len(set(v)) < 10]
    if weak:
        raise RuntimeError(f"Secret thiếu hoặc yếu (cần >= 32 ký tự ngẫu nhiên): {', '.join(weak)}")
    if ACCESS_SECRET == REFRESH_SECRET:
        raise RuntimeError("ACCESS_SECRET và REFRESH_SECRET phải khác nhau")
    if not SEC_DATABASE_URL:
        raise RuntimeError("Thiếu SEC_DATABASE_URL (CSDL người dùng / phiên)")


check_auth_config()


def llm_on() -> bool:
    """Bật khi ít nhất một hãng có khoá (trang quản trị sec.llm_providers hoặc biến môi trường)."""
    if LLM_ENABLED == "off":
        return False
    if LLM_ENABLED == "on":
        return True
    from app.chat.llm import keys  # nhập muộn: llmkeys -> secdb -> config
    return keys().has_active()

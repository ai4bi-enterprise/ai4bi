"""Kiểm thử bản Lite: không LLM, không đăng nhập, chỉ dữ liệu mẫu SQLite.

Đặt biến môi trường TRƯỚC khi import app (load_dotenv không ghi đè biến đã có).
"""
import os

os.environ["AI4BI_EDITION"] = "lite"
os.environ["LLM_ENABLED"] = "off"
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DATABASE_URL", "VERCEL"):
    os.environ.pop(k, None)

#!/usr/bin/env bash
# Khởi động AI4BI Chatbot (Mac/Linux): ./run_demo.sh
set -euo pipefail
cd "$(dirname "$0")"
echo "==================================================="
echo "  KHỞI ĐỘNG HỆ THỐNG AI4BI CHATBOT (SAMPLE DATA)"
echo "==================================================="
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || { echo "[!] Chưa cài Python 3.10+"; exit 1; }
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[i] Đã tạo .env từ .env.example – mặc định Rule Router, KHÔNG cần API key."
fi
if [ ! -d .venv ]; then
  echo "[1/3] Tạo môi trường ảo .venv ..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "[2/3] Cài đặt thư viện ..."
pip install -r requirements.txt --quiet --disable-pip-version-check
PORT=${PORT:-8000}
echo "[3/3] WebUI: http://localhost:${PORT}"
( sleep 2; (command -v xdg-open >/dev/null && xdg-open "http://localhost:${PORT}") || (command -v open >/dev/null && open "http://localhost:${PORT}") || true ) >/dev/null 2>&1 &
exec python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"

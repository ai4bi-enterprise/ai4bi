#!/usr/bin/env bash
# AI4BI Lite (Mac/Linux): ./run_demo.sh
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || { echo "[!] Cần Python 3.10+"; exit 1; }
[ -f .env ] || cp .env.example .env
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt --quiet --disable-pip-version-check
[ -f data/aimoingay_demo.db ] || { python tools/fetch_public_data.py && python tools/build_demo_db.py; }
PORT=${PORT:-8000}
echo "AI4BI Lite: http://localhost:${PORT}"
( sleep 2; (command -v xdg-open >/dev/null && xdg-open "http://localhost:${PORT}") || (command -v open >/dev/null && open "http://localhost:${PORT}") || true ) >/dev/null 2>&1 &
exec python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"

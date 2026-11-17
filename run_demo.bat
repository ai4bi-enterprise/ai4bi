@echo off
rem AI4BI Lite - nhap dup de chay (Windows)
cd /d %~dp0
if not exist .venv (py -3 -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt)
if not exist .env copy .env.example .env >nul
if not exist dataimoingay_demo.db (.venv\Scripts\python toolsetch_public_data.py && .venv\Scripts\python toolsuild_demo_db.py)
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --port 8000

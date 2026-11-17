@echo off
rem AI4BI Lite - nhap dup de chay (Windows, can Python 3.10+). Khong muon cai Python: tai ban ZIP o muc Releases.
cd /d "%~dp0"
if not exist .venv (py -3 -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt)
if not exist .env copy .env.example .env >nul
if not exist data\aimoingay_demo.db (.venv\Scripts\python tools\fetch_public_data.py && .venv\Scripts\python tools\build_demo_db.py)
.venv\Scripts\python launch.py
if errorlevel 1 pause

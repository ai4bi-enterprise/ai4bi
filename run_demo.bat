@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===================================================
echo   KHOI DONG HE THONG AI4BI CHATBOT (SAMPLE DATA)
echo ===================================================

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Chua cai Python. Tai tai https://www.python.org/downloads/ ^(tick "Add Python to PATH"^)
    pause
    exit /b 1
)

if not exist .env (
    copy .env.example .env >nul
    echo [i] Da tao file .env tu .env.example.
    echo [i] Mac dinh chay Rule Router - KHONG can API key.
    echo [i] Muon dung LLM mien phi: mo .env, dien GEMINI_API_KEY va/hoac GROQ_API_KEY roi chay lai.
)

echo [1/2] Kiem tra va cai dat thu vien...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [!] Cai dat thu vien that bai. Kiem tra ket noi mang.
    pause
    exit /b 1
)

echo [2/2] Dang mo giao dien WebUI tai http://localhost:8000 ...
start "" http://localhost:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause

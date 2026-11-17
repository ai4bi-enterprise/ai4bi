"""Đóng gói AI4BI thành file ZIP chạy ngay trên Windows (kèm Python, không cần cài gì, không cần quyền admin).

    python tools/build_windows_zip.py --embed python-3.14.x-embed-amd64.zip --out dist/AI4BI_Lite_Windows.zip
    python tools/build_windows_zip.py --smoke dist/AI4BI_Lite_Windows.zip      # giải nén + chạy thử + nạp file ví dụ

Chạy trên GitHub Actions (windows-latest) khi phát hành; Python dùng để chạy script phải CÙNG phiên bản với gói embed.
Tránh PyInstaller (.exe một file hay bị phần mềm diệt virus / SmartScreen chặn nhầm).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "AI4BI"
INCLUDE = ["app", "static", "config", "data/aimoingay_demo.db", "data/samples", "launch.py", "requirements.txt",
           "LICENSE", "README.md", "CHANGELOG.md", ".env.example"]
BAT = r"""@echo off
chcp 65001 >nul
title AI4BI - Dashboard + Tro ly AI
cd /d "%~dp0"
"python\python.exe" launch.py
if errorlevel 1 (
  echo.
  echo AI4BI dung vi loi o tren. Chup man hinh cua so nay gui bo phan ho tro.
  pause
)
"""
GUIDE = """AI4BI – HƯỚNG DẪN NHANH
========================

1. Giải nén toàn bộ file ZIP ra một thư mục (VD: D:\\AI4BI). Không chạy trực tiếp bên trong file ZIP.
2. Nhấp đúp AI4BI.bat. Trình duyệt tự mở http://127.0.0.1:8765 (cổng có thể khác nếu 8765 đang bận).
   Windows có thể hỏi "Windows protected your PC": bấm "More info" → "Run anyway".
3. Lúc đầu AI4BI hiện DỮ LIỆU MẪU. Để xem số của doanh nghiệp bạn:
   menu "Dữ liệu của tôi" → tải file mẫu Excel → điền → tải lên → "Dùng dữ liệu này".
   Muốn thử nhanh: tải "file ví dụ đã điền sẵn" rồi tải ngược lên.
4. Giữ cửa sổ đen mở khi dùng. Đóng cửa sổ đó = tắt AI4BI.

Dữ liệu bạn nạp chỉ nằm trên máy này, trong thư mục data\\user. Xoá thư mục đó (hoặc bấm
"Xoá dữ liệu của tôi khỏi máy") là xoá sạch. AI4BI không gửi dữ liệu ra ngoài; Trợ lý AI chạy
bằng bộ hiểu câu theo luật, chỉ dùng LLM khi bạn tự điền khoá API vào file .env.

Hỗ trợ / bản Pro: https://github.com/ai4bi-enterprise/ai4bi
"""


def run(*cmd, **kw):
    print("›", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True, **kw)


def build(embed: Path, out: Path) -> None:
    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    if f"-{sys.version_info.major}.{sys.version_info.minor}." not in embed.name:
        sys.exit(f"Gói embed {embed.name} phải cùng phiên bản với Python đang chạy ({sys.version.split()[0]})")
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / NAME
        app.mkdir()
        for item in INCLUDE:
            src = ROOT / item
            if src.is_dir():
                shutil.copytree(src, app / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif src.exists():
                (app / item).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, app / item)
        if (app / ".env.example").exists():
            shutil.copy2(app / ".env.example", app / ".env")
        py = app / "python"
        with zipfile.ZipFile(embed) as z:
            z.extractall(py)
        (py / f"python{ver}._pth").write_text(f"python{ver}.zip\n.\nLib\\site-packages\n..\nimport site\n", encoding="ascii")
        run(sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile", "--only-binary=:all:",
            "--target", str(py / "Lib" / "site-packages"), "-r", str(ROOT / "requirements.txt"))
        (app / "AI4BI.bat").write_bytes(BAT.replace("\n", "\r\n").encode("ascii"))
        (app / "HUONG_DAN.txt").write_bytes(b"\xef\xbb\xbf" + GUIDE.replace("\n", "\r\n").encode("utf-8"))
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in sorted(app.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(tmp))
    print(f"Đã đóng gói {out} ({out.stat().st_size / 1024 / 1024:.1f} MB)")


def _req(url: str, data: bytes | None = None, method: str = "GET", headers: dict | None = None) -> dict:
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read())


def smoke(zip_path: Path) -> None:
    """Giải nén như khách, chạy bằng Python đi kèm, nạp file ví dụ, so doanh thu dashboard với tổng tính từ file."""
    import openpyxl
    port = 8799
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        app = Path(tmp) / NAME
        env = {**os.environ, "AI4BI_PORT": str(port), "AI4BI_NO_BROWSER": "1"}
        for k in ("DATABASE_URL", "AUTH_MODE", "PYTHONPATH", "PYTHONHOME"):
            env.pop(k, None)
        proc = subprocess.Popen([str(app / "python" / "python.exe"), "launch.py"], cwd=app, env=env)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(120):
                try:
                    h = _req(base + "/api/health")
                    break
                except OSError:
                    if proc.poll() is not None:
                        sys.exit("Máy chủ trong gói ZIP dừng ngay khi khởi động")
                    time.sleep(1)
            else:
                sys.exit("Máy chủ trong gói ZIP không khởi động được")
            print("health:", h)
            sample = (app / "data" / "samples" / "ChuoiCaPhe_ViDu.xlsx").read_bytes()
            rep = _req(base + "/api/data/upload", sample, "POST",
                       {"x-file-name": "ChuoiCaPhe_ViDu.xlsx", "content-type": "application/octet-stream"})
            assert rep["can_activate"], rep["errors"]
            _req(base + "/api/data/activate", b"", "POST")
            wb = openpyxl.load_workbook(app / "data" / "samples" / "ChuoiCaPhe_ViDu.xlsx", read_only=True)
            rows = list(wb["BanHang"].iter_rows(values_only=True))
            wb.close()
            h = {v: i for i, v in enumerate(rows[0])}
            want = sum(r[h["Doanh thu thuần"]] for r in rows[1:] if r[h["Ngày"]].endswith("/10/2026"))
            d = _req(base + "/api/dashboard/sales?year=2026&month=10&mode=month")
            got = next(k for k in d["kpis"] if k["key"] == "net_revenue")["value"]
            print(f"Doanh thu 10/2026: file {want:,.0f} – dashboard {got:,.0f}")
            assert abs(got - want) < 1, "Dashboard lệch file"
            a = _req(base + "/api/chat/ask", json.dumps({"question": "Doanh thu tháng 10 năm 2026"}).encode(), "POST",
                     {"content-type": "application/json"})
            assert abs(next(k for k in a["kpis"] if k["key"] == "net_revenue")["value"] - want) < 1, "Chatbot lệch file"
            print("[OK] Gói ZIP chạy được, nạp file ví dụ, dashboard + chatbot khớp file")
        finally:
            proc.terminate()
            proc.wait(timeout=30)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")   # console Windows (cp1252) không in được tiếng Việt
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--embed", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "AI4BI_Lite_Windows.zip")
    ap.add_argument("--smoke", type=Path)
    a = ap.parse_args()
    if a.smoke:
        smoke(a.smoke)
    elif a.embed:
        build(a.embed, a.out)
    else:
        ap.error("cần --embed hoặc --smoke")

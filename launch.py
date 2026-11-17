"""Mở AI4BI trên máy: chọn cổng trống, khởi động máy chủ, tự mở trình duyệt khi đã sẵn sàng.

    python launch.py                 # bản ZIP Windows gọi file này qua AI4BI.bat
Biến môi trường: AI4BI_PORT (cổng cố định), AI4BI_NO_BROWSER=1 (không mở trình duyệt – dùng khi kiểm thử).
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def free_port(start: int) -> int:
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("Không tìm được cổng trống để chạy AI4BI.")


def open_when_ready(url: str) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2):
                break
        except OSError:
            time.sleep(0.5)
    else:
        print("AI4BI khởi động chậm – hãy mở thủ công:", url, flush=True)
        return
    print(f"\n  ✔ AI4BI đã sẵn sàng: {url}\n  Giữ cửa sổ này mở khi dùng. Đóng cửa sổ = tắt AI4BI.\n", flush=True)
    if os.getenv("AI4BI_NO_BROWSER") != "1":
        webbrowser.open(url)


def main() -> None:
    port = int(os.getenv("AI4BI_PORT") or free_port(8765))
    url = f"http://127.0.0.1:{port}/"
    print("  AI4BI – Dashboard điều hành + Trợ lý AI")
    print("  Đang khởi động… (lần đầu có thể mất 10–20 giây)", flush=True)
    threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

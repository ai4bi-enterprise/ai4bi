"""
Chụp ảnh minh họa cho README (docs/*.png, docs/demo_overview.gif) bằng Playwright.

Chuẩn bị:  pip install -r requirements-dev.txt && python -m playwright install chromium
Chạy:      (cửa sổ 1) python -m uvicorn app.main:app --port 8000
           (cửa sổ 2) python tools/capture_docs.py
Biến môi trường tùy chọn: AI4BI_URL (mặc định http://localhost:8000), CHROMIUM_PATH.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

# Console Windows mặc định dùng cp1252 -> in tiếng Việt sẽ lỗi UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
URL = os.getenv("AI4BI_URL", "http://localhost:8000")
Q_DESC = "Doanh thu và biên lợi nhuận gộp theo từng chi nhánh trong Quý 3?"
Q_BOD = "Tại sao doanh thu Quý 3 vượt KPI 18% nhưng dòng tiền thực thu lại sụt giảm?"

ARCH_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font-family:'Segoe UI',Inter,'DejaVu Sans',sans-serif;background:#f4f6fb}
.wrap{width:1400px;height:540px;padding:36px 40px;box-sizing:border-box}
h1{margin:0 0 4px;font-size:26px;color:#0b1730}.sub{color:#64748b;margin-bottom:26px;font-size:15px}
.row{display:flex;align-items:stretch;gap:12px}.arrow{align-self:center;color:#94a3b8;font-size:24px;font-weight:700}
.box{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:14px 14px;box-shadow:0 6px 20px rgba(15,23,42,.06);width:170px;box-sizing:border-box}
.box b{display:block;font-size:14.5px;color:#0b1730;margin-bottom:6px}.box span{font-size:12px;color:#475569;line-height:1.45;display:block}
.k{font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px}
.blue .k{color:#2563eb}.green .k{color:#059669}.amber .k{color:#b45309}.navy{background:#0b1730;border-color:#0b1730}.navy b{color:#fff}.navy span{color:#cbd5e1}.navy .k{color:#38bdf8}
.amber{border-color:#fcd34d;background:#fffbeb}
.lane{margin-top:26px;display:flex;gap:18px}
.note{flex:1;background:#fff;border-radius:14px;border:1px dashed #cbd5e1;padding:14px 18px;font-size:13.5px;color:#334155;line-height:1.55}
.note b{color:#0b1730}.pv{border-color:#ddd6fe;background:#faf7ff}
</style></head><body><div class="wrap">
<h1>AI4BI — Kiến trúc Community Edition v1.2 (Dashboard + Trợ lý AI)</h1>
<div class="sub">LLM chỉ hiểu câu hỏi. Con số và phân tích luôn đi từ Semantic Layer → SQL tham số hóa → Database (read-only) → Formatter & Insight Engine tất định.</div>
<div class="row">
 <div class="box navy"><div class="k">WebUI</div><b>Dashboard + Chat</b><span>Thẻ KPI có nút Hỏi AI → panel chat · câu hỏi tiếng Việt</span></div><div class="arrow">→</div>
 <div class="box blue"><div class="k">Router</div><b>Gemini → Groq → Luật</b><span>LLM miễn phí (tùy chọn) · Rule Router không cần key</span></div><div class="arrow">→</div>
 <div class="box blue"><div class="k">Intent JSON</div><b>Chỉ số · Chiều · Lọc</b><span>Chỉ chứa TÊN đã khai báo — không SQL, không số</span></div><div class="arrow">→</div>
 <div class="box green"><div class="k">Compiler</div><b>Kiểm duyệt + SQL</b><span>Đối chiếu Catalog · bind tham số · SQLite read-only</span></div><div class="arrow">→</div>
 <div class="box navy"><div class="k">Formatter</div><b>Bảng · Biểu đồ</b><span>Số liệu tất định + SQL đã chạy</span></div><div class="arrow">→</div>
 <div class="box amber"><div class="k">Insight Engine</div><b>Phân tích 5 mục</b><span>Nhận xét · Đánh giá · Kết luận · Đề xuất · Gợi ý (theo ngưỡng YAML)</span></div>
</div>
<div class="lane">
 <div class="note"><b>Semantic Layer (YAML)</b><br>19 chỉ số · chiều tốt/xấu · ngưỡng <i>assess</i> · mẫu <i>actions</i> · chỉ số <i>related</i> · hướng <i>drill</i>. Đổi ngưỡng/đề xuất không cần sửa code.</div>
 <div class="note pv"><b>BOD Deep-Dive (Demo)</b><br>Câu hỏi “Tại sao…” hoặc bật toggle → 2 kịch bản dựng sẵn, số liệu vẫn truy vấn thật, cùng bố cục 5 mục.<br><b>Enterprise:</b> Multi-step Planner + Root-Cause Engine.</div>
 <div class="note"><b>Kiểm chứng</b><br>Mọi câu Gợi ý được Router + Compiler chạy thử trước khi hiển thị · 74 test tự động · verify_story.py đối soát độc lập.</div>
</div></div></body></html>"""


async def main() -> None:
    DOCS.mkdir(exist_ok=True)
    frames: list[Image.Image] = []
    async with async_playwright() as p:
        kw = {"args": ["--no-sandbox"]}
        if os.getenv("CHROMIUM_PATH"):
            kw["executable_path"] = os.getenv("CHROMIUM_PATH")
        browser = await p.chromium.launch(**kw)

        # --- Sơ đồ kiến trúc
        pg = await browser.new_page(viewport={"width": 1400, "height": 540}, device_scale_factor=1.5)
        await pg.set_content(ARCH_HTML)
        await pg.screenshot(path=str(DOCS / "architecture.png"))
        print("[capture] docs/architecture.png")

        # --- Dashboard + panel chatbot
        dp = await browser.new_page(viewport={"width": 1440, "height": 900})
        await dp.goto(f"{URL}/?mode=qtd&month=9")
        await dp.wait_for_selector(".card.branch")
        await dp.wait_for_timeout(500)
        await dp.screenshot(path=str(DOCS / "screenshot_dashboard.png"), full_page=True)
        print("[capture] docs/screenshot_dashboard.png")
        await dp.locator(".card.branch").nth(2).locator(".ask").click()
        await dp.frame_locator("#cpFrame").locator(".ins").wait_for(timeout=20000)
        await dp.wait_for_timeout(900)
        await dp.screenshot(path=str(DOCS / "screenshot_dashboard_panel.png"))
        print("[capture] docs/screenshot_dashboard_panel.png")
        # Social preview (1280x640): Dashboard + panel Trợ lý AI
        social = await dp.screenshot(clip={"x": 0, "y": 0, "width": 1440, "height": 720})
        Image.open(io.BytesIO(social)).convert("RGB").resize((1280, 640), Image.LANCZOS).save(DOCS / "social_preview.png")
        print("[capture] docs/social_preview.png")
        await dp.close()

        # --- Trang Trợ lý AI
        pg = await browser.new_page(viewport={"width": 1440, "height": 900})
        await pg.goto(f"{URL}/chat")
        await pg.wait_for_selector(".chip")
        await pg.wait_for_timeout(400)

        async def grab(hold: int = 1):
            img = Image.open(io.BytesIO(await pg.screenshot())).convert("RGB").resize((1080, 675), Image.LANCZOS)
            frames.extend([img] * hold)

        await grab(4)

        async def ask(q: str, bod: bool = False):
            n = await pg.locator(".msg .card .meta").count()
            if bod != await pg.is_checked("#bodMode"):
                await pg.click(".bod-toggle")
            await pg.click("#input")
            for i in range(0, len(q), 6):
                await pg.keyboard.type(q[i:i + 6])
                if i % 18 == 0:
                    await grab(1)
            await grab(1)
            await pg.keyboard.press("Enter")
            await pg.wait_for_function(f"document.querySelectorAll('.msg .card .meta').length > {n}")
            await pg.wait_for_timeout(900)

        async def card_shot(name: str):
            await pg.set_viewport_size({"width": 1440, "height": 3000})
            await pg.wait_for_timeout(400)
            await pg.locator(".msg .card").last.screenshot(path=str(DOCS / name))
            await pg.set_viewport_size({"width": 1440, "height": 900})
            await pg.wait_for_timeout(300)
            print(f"[capture] docs/{name}")

        await ask(Q_DESC)
        await grab(5)
        await card_shot("screenshot_descriptive.png")
        await pg.locator(".msg .card").last.scroll_into_view_if_needed()
        for _ in range(5):
            await pg.mouse.wheel(0, 300)
            await pg.wait_for_timeout(300)
            await grab(3)

        await ask(Q_BOD)
        await grab(5)
        for _ in range(7):
            await pg.mouse.wheel(0, 340)
            await pg.wait_for_timeout(300)
            await grab(3)
        await card_shot("screenshot_bod.png")
        await browser.close()

    frames[0].save(DOCS / "demo_overview.gif", save_all=True, append_images=frames[1:], duration=420, loop=0,
                   optimize=True)
    print(f"[capture] docs/demo_overview.gif ({len(frames)} khung hình, "
          f"{(DOCS / 'demo_overview.gif').stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    asyncio.run(main())

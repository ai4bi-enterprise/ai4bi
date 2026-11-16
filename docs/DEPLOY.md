# Triển khai Live Demo

## Phương án A — Render.com (miễn phí, đang dùng cho Live Demo)

1. Đăng ký <https://render.com> bằng tài khoản GitHub.
2. *New → Web Service → Public Git Repository* → `https://github.com/ai4bi-enterprise/ai4bi`.
3. Language **Docker**, Region **Singapore**, Compute **Free ($0/month)**.
4. Environment Variables: `PORT=7860`, `DEMO_RATE_LIMIT_PER_DAY=15`, `CONTACT_URL=<link liên hệ>`
   (tùy chọn `GEMINI_API_KEY`, `GROQ_API_KEY`, `LLM_PROVIDERS=gemini,groq`).
5. Bấm **Deploy web service**. Khi push code mới lên GitHub: vào service → *Manual Deploy → Deploy latest commit*.

> Gói Free tự ngủ sau 15 phút không có truy cập; lần mở kế tiếp mất ~1 phút để khởi động. 750 giờ/tháng.

## Phương án A2 — Hugging Face Spaces (Docker Space hiện yêu cầu gói PRO trả phí)

1. Tạo Space mới: **SDK = Docker**, mẫu *Blank*, phần cứng *CPU basic*.
2. Thêm khối cấu hình vào **đầu** `README.md` của Space:

   ```yaml
   ---
   title: AI4BI Live Demo
   emoji: 📊
   colorFrom: blue
   colorTo: purple
   sdk: docker
   app_port: 7860
   ---
   ```

3. *Settings → Variables and secrets*:
   - Secret `GEMINI_API_KEY`, `GROQ_API_KEY` (tùy chọn — không có key vẫn chạy Rule Router).
   - Variable `LLM_PROVIDERS=gemini,groq`, `DEMO_RATE_LIMIT_PER_DAY=15`, `CONTACT_URL=<link liên hệ>`.
   - Dùng key free tier (không gắn billing) => hết quota chỉ trả 429, không phát sinh chi phí.
4. Đẩy toàn bộ repo (đã có `Dockerfile`, cổng 7860) lên Space.

## Phương án B — Máy chủ sẵn có + Cloudflare Tunnel

```bash
docker build -t ai4bi-chatbot .
docker run -d --restart unless-stopped -p 127.0.0.1:8000:7860 --env-file .env --name ai4bi ai4bi-chatbot
cloudflared tunnel --url http://localhost:8000
```

## Checklist trước khi push lên GitHub

- [ ] `.env` không nằm trong commit (`git status` không thấy `.env`).
- [ ] `pytest -q` xanh (74 ca), `python tools/verify_story.py` báo **ĐẠT**.
- [ ] `python tools/check_llm.py` báo **ĐẠT** cho từng provider trong `LLM_PROVIDERS` (nếu dùng LLM).
- [ ] Ảnh `docs/` đã cập nhật (`python tools/capture_docs.py`).
- [ ] Đặt email commit dạng `ID+username@users.noreply.github.com` (ẩn email cá nhân).
- [ ] Topics: `ai4bi, text-to-sql, business-intelligence, semantic-layer, dashboard, fastapi, bi-chatbot`.
- [ ] Upload `docs/social_preview.png` vào *Settings → Social preview*; tạo Release `v1.2.0`.

```bash
git init
git add .
git commit -m "feat: AI4BI Community Edition v1.2.0 - dashboard + chatbot panel + insights"
git branch -M main
git remote add origin https://github.com/ai4bi-enterprise/ai4bi.git
git push -u origin main
```

# Đóng góp cho AI4BI Lite

Cảm ơn bạn! Quy trình:

1. Mở Issue mô tả lỗi / đề xuất trước khi gửi Pull Request lớn.
2. Fork, tạo nhánh, viết code kèm kiểm thử: `pip install -r requirements-dev.txt && pytest -q`.
3. Nguyên tắc bắt buộc:
   - **Không để LLM viết SQL hay tạo con số.** Mọi số đi qua `app/core/semantic.py`.
   - Chỉ số mới khai báo trong `app/semantic/metrics.yaml`, không viết SQL rời trong phân hệ.
   - Chatbot và dashboard phải ra cùng một con số (xem `tests/test_lite.py::test_chat_equals_dashboard`).
4. Gửi Pull Request vào nhánh `main`.

Ghi chú: repo này được đồng bộ tự động từ repo phát triển chính. PR được chấp nhận sẽ được đưa vào repo chính và xuất hiện lại ở đây trong lần đồng bộ kế tiếp.

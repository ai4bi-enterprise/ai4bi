# Lịch sử thay đổi

## v2.1.0 – Thử với dữ liệu của bạn + bản Windows chạy ngay
- **Dữ liệu của tôi**: tải file mẫu Excel, nạp doanh thu / kế hoạch của doanh nghiệp bạn; kiểm từng dòng, báo lỗi theo sheet / dòng / cột,
  đối soát tổng file = tổng đã nạp; dashboard và Trợ lý AI chạy ngay trên số của bạn. Dữ liệu chỉ nằm trên máy, xoá được bằng một nút.
- File ví dụ đã điền sẵn (chuỗi cà phê 3 cửa hàng) để thử toàn bộ quy trình.
- **AI4BI_Lite_Windows.zip**: giải nén, nhấp đúp `AI4BI.bat` – đã kèm Python, không cần cài đặt, tự mở trình duyệt.
- Phân hệ Doanh thu không còn gắn với kênh "Đại lý" của dữ liệu mẫu: kênh trọng tâm và kênh có kế hoạch lấy từ dữ liệu.
- Sửa `run_demo.bat` (đường dẫn hỏng).

## v2.0.0 – AI4BI Lite
Thế hệ mới, viết lại toàn bộ trên kiến trúc *AI4BI Enterprise Blueprint*. Đổi tên phiên bản: Community → **Lite**.
- Dashboard **Tổng quan + Doanh thu** và **Trợ lý AI** dùng chung một lớp ngữ nghĩa YAML – chatbot = dashboard (có test đối chiếu).
- Cổng kiểm số: lời nhận xét có con số không nằm trong bằng chứng bị chặn.
- LLM tuỳ chọn, thứ tự dự phòng cố định Anthropic → OpenAI → Gemini; không có khoá vẫn chạy theo luật.
- Dữ liệu mẫu mới dựng từ Microsoft AdventureWorks DW (MIT), 01/2024–09/2026, theo hợp đồng dữ liệu `bi_*`.
- Phân hệ P&L, Bảng cân đối, Lưu chuyển tiền, Tồn kho hiện trên menu với nhãn 🔒 PRO.

## v1.2.0 – Community Edition
- Revenue Dashboard, panel Trợ lý AI, Nhận xét · Đánh giá · Đề xuất theo quy tắc (bản cũ – xem lịch sử git).

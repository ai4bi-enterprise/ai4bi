# Changelog

## v1.2.0
- **Revenue Dashboard** tại `/`: thẻ Tổng công ty (tiến độ & trạng thái KPI), 4 thẻ KPI, 3 thẻ chi nhánh (Kỳ trước · Thực hiện · KPI, donut tỷ trọng), xu hướng tháng + dòng tiền, nhóm hàng, Top 10 khách hàng/sản phẩm; toggle MTD/QTD/YTD.
- **Panel Trợ lý AI** trượt bên phải; mỗi thẻ có nút *Hỏi AI* gửi câu hỏi theo ngữ cảnh (đã kiểm chứng chạy được).
- Số liệu dashboard tính qua cùng Compiler/Semantic Layer với chatbot (có test đối chiếu).
- Trang chat chuyển sang `/chat`, hỗ trợ chế độ nhúng `?embed=1`.

## v1.1.0
- Mỗi câu trả lời có **Nhận xét · Đánh giá · Kết luận · Đề xuất · Gợi ý** (Insight Engine theo quy tắc, không dùng LLM).
- YAML bổ sung `assess`, `actions`, `related`, `drill`; Catalog kiểm tra tính hợp lệ khi khởi động.
- BOD Deep-Dive thống nhất bố cục 5 mục.
- Sửa Rule Router: “nợ quá hạn >90 ngày” không còn bị hiểu thành bộ lọc nhóm tuổi nợ; “loại chi phí” không còn bị hiểu thành chỉ số chi phí.

## v1.0.0
- Phát hành Community Edition: Semantic Layer YAML, Rule Router, LLM Router miễn phí (Gemini → Groq), WebUI, BOD Deep-Dive demo, dữ liệu mẫu SQLite.

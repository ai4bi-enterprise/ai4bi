# Hợp đồng dữ liệu – kết nối dữ liệu thật

Dashboard và chatbot **chỉ đọc** các đối tượng `bi_*`. Bản demo là bảng SQLite; với khách hàng, tạo **VIEW cùng tên, cùng cột** trên kho của họ. Định nghĩa đầy đủ: [`tools/contract_schema.sql`](../tools/contract_schema.sql).

## Quy ước chung

- Tiền lưu bằng **đồng**, không làm tròn; định dạng "tỷ/triệu" làm ở đúng một chỗ (`app/core/fmt.py`, `static/js/render.js`).
- Mọi bảng có `year`, `quarter`, `month`; bảng chi tiết bán hàng có thêm `date`.
- Bảng có `org_code` để áp phạm vi người dùng. Nguồn không có cột này thì người bị giới hạn phạm vi **không thấy** nguồn đó (fail-closed).
- Không đưa cột cá nhân (điện thoại, email, CCCD, mã số thuế) vào view.

| Đối tượng | Hạt (grain) | Ghi chú |
|---|---|---|
| `bi_meta` | key/value | `company_name`, `as_of_date`, `last_closed_month` (YYYY-MM), `source`, `transform_note` |
| `bi_org` | 1 dòng / đơn vị | khu vực → đơn vị, thứ tự hiển thị |
| `bi_sales` | 1 dòng hoá đơn | `net_revenue` đã trừ chiết khấu; `cogs` giá vốn dòng |
| `bi_sales_plan` | tháng × đơn vị × kênh | chỉ so % kế hoạch trên các (đơn vị × kênh) có giao kế hoạch |
| `bi_pnl` + `bi_pnl_lines` | tháng × đơn vị × kịch bản × dòng lá | dấu theo tác động lợi nhuận (+ thu, − chi); `scenario` = actual / plan |
| `bi_cost_detail` | tháng × đơn vị × tài khoản chi phí | số dương = chi phí |
| `bi_bs` + `bi_bs_lines` | số dư cuối tháng × đơn vị × dòng | `grp` A/L/E; phải thoả Tài sản = Nợ + Vốn |
| `bi_cashflow` + `bi_cf_lines` | tháng × đơn vị × dòng | `grp` CFO/CFI/CFF; tổng = thay đổi tiền trên BS |
| `bi_inventory` | cuối tháng × kho × mặt hàng | `value_end` = số lượng × giá vốn; `safety_stock`, `reorder_point` |

## Mẫu ánh xạ P&L theo hệ thống tài khoản Việt Nam (TT200 / TT99) – SQL Server

```sql
CREATE OR ALTER VIEW dbo.bi_pnl AS
SELECT year, quarter, month, org_code, 'actual' AS scenario, line_code, SUM(amount) AS amount
FROM (
  SELECT YEAR(g.ngay_hach_toan) AS year, DATEPART(QUARTER, g.ngay_hach_toan) AS quarter,
         MONTH(g.ngay_hach_toan) AS month, g.ma_don_vi AS org_code,
         CASE
           WHEN g.tk_co LIKE '511%' THEN 'DT_BAN_HANG'
           WHEN g.tk_no LIKE '521%' THEN 'GIAM_TRU'
           WHEN g.tk_no LIKE '632%' THEN 'GIA_VON'
           WHEN g.tk_co LIKE '214%' THEN 'KHAU_HAO'
           WHEN g.tk_no LIKE '641%' THEN 'CP_BH'
           WHEN g.tk_no LIKE '642%' THEN 'CP_QLDN'
           WHEN g.tk_co LIKE '515%' THEN 'DT_TC'
           WHEN g.tk_no LIKE '635%' THEN 'CP_LAI_VAY'
           WHEN g.tk_co LIKE '711%' OR g.tk_no LIKE '811%' THEN 'TN_KHAC'
           WHEN g.tk_no LIKE '821%' THEN 'THUE_TNDN'
         END AS line_code,
         -- dấu theo tác động lợi nhuận: ghi Có TK thu nhập (+), ghi Nợ TK chi phí (-)
         CASE WHEN g.tk_co LIKE '5%' OR g.tk_co LIKE '7%' THEN g.so_tien ELSE -g.so_tien END AS amount
  FROM dbo.so_cai AS g                      -- bảng sổ cái của khách (tên cột chỉ là ví dụ)
  WHERE g.tk_no NOT LIKE '911%' AND g.tk_co NOT LIKE '911%'   -- bỏ bút toán kết chuyển
) AS x
WHERE line_code IS NOT NULL
GROUP BY year, quarter, month, org_code, line_code;
GO
GRANT SELECT ON dbo.bi_pnl TO [ai4bi_ro];
```

Tên bảng/cột phía khách chỉ là ví dụ – lấy đúng tên từ hệ thống của họ. Sau khi tạo view, chạy `pytest` để kiểm cân đối và đối soát trước khi cho người dùng xem.

## Kết nối

```ini
# .env
DATABASE_URL=mssql+pyodbc://ai4bi_ro:***@server/kho?driver=ODBC+Driver+18+for+SQL+Server
# hoặc postgresql+psycopg://ai4bi_ro:***@host/kho
```

Login phải **chỉ đọc** các view `bi_*`. Chuỗi kết nối chỉ nằm trong biến môi trường, không ghi vào code hay log.

Nếu view của khách đặt tên khác, sửa `table:` trong `app/semantic/sources.yaml` – không sửa code.

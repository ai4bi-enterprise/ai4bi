-- =====================================================================
-- HỢP ĐỒNG DỮ LIỆU AI4BI – AI Mỗi Ngày
-- Dashboard và chatbot CHỈ đọc các đối tượng bi_* dưới đây.
-- Bản demo: bảng SQLite. Khách hàng thật: tạo VIEW cùng tên, cùng cột trên
-- SQL Server / PostgreSQL / MySQL, ánh xạ từ ERP/kế toán của họ (xem docs/DATA_CONTRACT.md).
-- Quy ước: tiền lưu bằng đồng (VND), không làm tròn; thời gian có Year/Quarter/Month.
-- =====================================================================

CREATE TABLE bi_meta (key TEXT PRIMARY KEY, value TEXT);

-- Cây tổ chức: Khu vực -> Đơn vị (công ty / chi nhánh)
CREATE TABLE bi_org (
  org_code TEXT PRIMARY KEY, org_name TEXT, country TEXT,
  region_code TEXT, region_name TEXT, local_currency TEXT, sort_order INTEGER
);

-- Doanh thu: 1 dòng = 1 dòng hoá đơn
CREATE TABLE bi_sales (
  date TEXT, year INTEGER, quarter INTEGER, month INTEGER, org_code TEXT, channel TEXT,
  customer_code TEXT, customer_name TEXT, customer_type TEXT,
  product_code TEXT, product_name TEXT, category TEXT, subcategory TEXT, order_no TEXT,
  quantity INTEGER, gross_amount REAL, discount_amount REAL, net_revenue REAL, cogs REAL
);
CREATE INDEX ix_sales_ym ON bi_sales(year, month, org_code);

-- Kế hoạch doanh thu: tháng x đơn vị x kênh
CREATE TABLE bi_sales_plan (year INTEGER, quarter INTEGER, month INTEGER, org_code TEXT, channel TEXT, plan_revenue REAL);

-- P&L quản trị: dòng lá, dấu theo tác động lợi nhuận (+ thu, - chi); scenario = actual | plan
CREATE TABLE bi_pnl (year INTEGER, quarter INTEGER, month INTEGER, org_code TEXT, scenario TEXT, line_code TEXT, amount REAL);
CREATE TABLE bi_pnl_lines (line_code TEXT PRIMARY KEY, line_name TEXT, sort_order INTEGER);
-- Chi phí theo tài khoản (số dương = chi phí) để phân tích cơ cấu chi phí
CREATE TABLE bi_cost_detail (year INTEGER, quarter INTEGER, month INTEGER, org_code TEXT, line_code TEXT,
  account_code TEXT, account_name TEXT, amount REAL);

-- Bảng cân đối: số dư cuối tháng, số dương theo bản chất (Tài sản = Nợ + Vốn)
CREATE TABLE bi_bs (year INTEGER, quarter INTEGER, month INTEGER, org_code TEXT, line_code TEXT, amount REAL);
CREATE TABLE bi_bs_lines (line_code TEXT PRIMARY KEY, line_name TEXT, grp TEXT, sort_order INTEGER);

-- Lưu chuyển tiền tệ theo tháng (CFO / CFI / CFF); tổng = thay đổi tiền trên BS
CREATE TABLE bi_cashflow (year INTEGER, quarter INTEGER, month INTEGER, org_code TEXT, line_code TEXT, amount REAL);
CREATE TABLE bi_cf_lines (line_code TEXT PRIMARY KEY, line_name TEXT, grp TEXT, sort_order INTEGER);

-- Tồn kho: chụp cuối tháng theo mặt hàng x kho
CREATE TABLE bi_inventory (
  year INTEGER, quarter INTEGER, month INTEGER, warehouse TEXT, product_code TEXT, product_name TEXT,
  category TEXT, subcategory TEXT, units_in INTEGER, units_out INTEGER, units_end INTEGER,
  unit_cost REAL, value_end REAL, safety_stock INTEGER, reorder_point INTEGER
);

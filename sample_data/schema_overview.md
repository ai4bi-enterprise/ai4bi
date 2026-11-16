# Bộ dữ liệu mẫu: `enterprise_sample.db`

> **100% dữ liệu hư cấu.** Công ty giả lập: *Công ty CP Phân phối Hàng tiêu dùng Sao Việt*.
> Sinh bằng `python tools/generate_sample_data.py` (seed cố định = 2025, chạy lại cho file giống hệt).

| Thuộc tính | Giá trị |
|---|---|
| Kỳ dữ liệu | 01/01/2025 – 31/12/2025 (4 quý), ngày chốt 31/12/2025 — **không có năm 2024** nên không có so sánh cùng kỳ năm trước |
| Quy mô | 3 chi nhánh · 4 nhóm hàng · 20 SKU · 66 khách hàng · ~4.400 hóa đơn · ~15.600 dòng hàng |
| Doanh thu thuần | ~510 tỷ đồng/năm |
| Định dạng | SQLite 1 file (~2 MB), mở ở chế độ **chỉ đọc** |

## 1. Bảng nguồn (giả lập dữ liệu ERP)

| Bảng | Số dòng | Hạt dữ liệu | Cột chính |
|---|---:|---|---|
| `DanhMuc_ChiNhanh` | 3 | Chi nhánh | MaCN, TenCN, Vung, ThanhPho |
| `DanhMuc_SanPham` | 20 | SKU | MaSP, TenSP, MaNhom, TenNhom, GiaNiemYet, GiaVon, **TranChietKhau** |
| `DanhMuc_KhachHang` | 66 | Khách hàng | MaKH, TenKH, **LoaiKH**, MaCN, TinhThanh, SoNgayNo |
| `DoanhThu_HoaDon` | 15.640 | Dòng hóa đơn | SoHoaDon, NgayHoaDon, MaCN, MaKH, MaSP, SoLuong, DonGia, ThanhTienGop, ChietKhau, DoanhThuThuan, GiaVon |
| `ChiPhi_BanHang` | 180 | Tháng × Chi nhánh × Loại chi phí | Thang, MaCN, LoaiChiPhi, SoTien |
| `CongNo_KhachHang` | 4.569 | Chứng từ phải thu | SoChungTu, NgayChungTu, HanThanhToan, SoTien, NgayThanhToan, SoTienDaThu |
| `TonKho_SanPham` | 720 | Tháng × Chi nhánh × SKU | TonDauKy, NhapTrongKy, XuatTrongKy, TonCuoiKy, GiaTriTonCuoiKy |
| `KPI_DoanhThu` | 12 | **Quý** × Chi nhánh | Nam, Quy, MaCN, KPIDoanhThu |
| `Meta_DuLieu` | 3 | Khóa–giá trị | ten_cong_ty, ngay_chot_du_lieu, seed |

## 2. View ngữ nghĩa (Semantic Layer chỉ đọc từ các view này)

| View | Dùng cho |
|---|---|
| `v_sales` | Doanh thu, chiết khấu, giá vốn, lợi nhuận gộp |
| `v_expense` | Chi phí bán hàng & quản lý |
| `v_cash` | Dòng tiền thực thu (theo ngày thanh toán) |
| `v_kpi` | KPI doanh thu (hạt quý) |
| `v_ar` | Công nợ phải thu tại ngày chốt `:asof` |
| `v_inventory` | Tồn kho (dự phòng cho bản Pro) |

## 3. “Câu chuyện quản trị” cài sẵn (Quý 3/2025)

| | Quý 2 | Quý 3 | Biến động |
|---|---:|---:|---:|
| Doanh thu thuần | 120,69 tỷ | 142,41 tỷ | **+18,0%** (đạt 107,2% KPI) |
| Lợi nhuận thuần | 8,94 tỷ | 7,87 tỷ | **−12,0%** |
| Dòng tiền thực thu | 108,80 tỷ | 95,75 tỷ | **−12,0%** |

1. **Miền Nam × Nhóm A - Đồ uống**: chiết khấu đại lý 22% (trần 10%) → tỷ lệ CK tổ hợp 19,8%, biên LN gộp 22,7% → 8,2%; đóng góp ~89% mức tăng doanh thu; Q4 sụt mạnh (dồn hàng).
2. **Đại lý cấp 2** ngừng/khất thanh toán: nợ quá hạn >90 ngày từ 0,35 tỷ (30/06) lên 12,98 tỷ (30/09).

Đối soát độc lập: `python tools/verify_story.py`.

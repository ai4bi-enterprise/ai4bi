"""Registry phân hệ (Blueprint P10: mở rộng bằng đăng ký).

Thêm phân hệ mới = 1 file module có TITLE, KEYWORDS, build(period, user, filters) + 1 dòng ở đây
+ 1 mục trong MENU. Giao diện tự hiện menu, chatbot tự biết các khối để trả lời.
Bản Lite không có file của phân hệ Pro (config.PRO_MODULES): menu vẫn hiện mục đó kèm nhãn 🔒 PRO.
"""
from importlib import import_module

from app import config

ALL = ("overview", "sales", "pnl", "bs", "cashflow", "inventory")

REGISTRY = {k: import_module(f"app.modules.{k}") for k in ALL if k not in config.LOCKED_MODULES}

MENU = [  # nhóm menu, (khoá, nhãn, biểu tượng, có bộ lọc đơn vị)
    ("Điều hành", [("overview", "Tổng quan", "home", True)]),
    ("Kinh doanh", [("sales", "Doanh thu", "trend", True), ("inventory", "Tồn kho", "box", False)]),
    ("Tài chính", [("pnl", "Kết quả kinh doanh", "pnl", True), ("bs", "Bảng cân đối", "scale", True),
                   ("cashflow", "Lưu chuyển tiền", "cash", True)]),
]

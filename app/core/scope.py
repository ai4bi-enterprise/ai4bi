"""Người dùng và phạm vi dữ liệu – mặc định TỪ CHỐI, thực thi ở backend (Blueprint P3, chương 14).

AUTH_MODE=password (mặc định): người dùng trong sec.users, đăng nhập bằng mật khẩu (app/core/auth.py); quyền màn hình
+ phạm vi riêng từng người (app/core/access.py), vai trò trong config/users.yaml là bộ chọn sẵn. Admin thấy tất cả.
AUTH_MODE=demo: chọn hồ sơ bằng header X-Demo-User, không mật khẩu – chỉ trình diễn offline trên máy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml
from fastapi import Header, HTTPException, Request

from app import config


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: str
    modules: tuple[str, ...]
    orgs: frozenset[str] | None = None   # None = toàn bộ; frozenset() rỗng = không thấy gì
    regions: tuple[str, ...] = field(default_factory=tuple)
    email: str = ""
    user_id: int = 0
    is_admin: bool = False
    profile: str = ""
    perm_version: str = ""

    @property
    def is_all(self) -> bool:
        return self.orgs is None

    @property
    def is_preview(self) -> bool:   # [preview-v1] tài khoản xem thử chung (chỉ có ở chế độ đăng nhập)
        return bool(self.user_id) and not self.is_admin and is_preview_row({"profile": self.profile})

    def can(self, module: str) -> bool:
        if module in config.LOCKED_MODULES:  # bản Lite: phân hệ Pro không có
            return False
        if not (module in self.modules or "*" in self.modules):
            return False
        from app.core.dataset import has_data   # phân hệ chưa có dữ liệu (khách mới nạp 1 phần) -> ẩn
        return has_data(module)


@lru_cache(maxsize=1)
def users() -> dict[str, User]:
    raw = yaml.safe_load(open(config.USERS_FILE, encoding="utf-8"))
    out = {}
    for name, u in raw["users"].items():
        scope = u.get("scope")
        orgs = None if scope == "*" else frozenset(scope or [])
        out[name] = User(name, u["display_name"], u.get("role", ""), tuple(u.get("modules", [])), orgs)
    return out


def is_preview_row(row: dict) -> bool:
    return row.get("profile") == config.PREVIEW_PROFILE and not row.get("is_admin")


@lru_cache(maxsize=1)
def profiles_raw() -> dict[str, dict]:
    return yaml.safe_load(open(config.USERS_FILE, encoding="utf-8"))["users"]


def from_session(row: dict) -> User:
    """sec.users + quyền riêng (sec.user_dashboard_access, sec.user_scope) -> User.

    Mặc định từ chối: không có dòng quyền màn hình = không mở màn hình nào; không có dòng phạm vi = không thấy gì.
    Vai trò (hồ sơ trong config/users.yaml) chỉ còn là nhãn + bộ chọn sẵn trên trang quản trị.
    """
    from app.core import access, auth  # chỉ bản Pro (AUTH_MODE=password)
    base = users().get(row["profile"])
    acc = row.get("access") or {"modules": [], "scope": []}
    common = dict(username=row["email"], email=row["email"], user_id=row["id"], is_admin=bool(row["is_admin"]),
                  profile=row["profile"], perm_version=auth.perm_version(row), display_name=row["display_name"])
    if row["is_admin"]:  # Blueprint 14.3: admin thấy tất cả
        return User(role=base.role if base else "Quản trị", modules=("*",), orgs=None, **common)
    return User(role=base.role if base else "", modules=tuple(acc["modules"]), orgs=access.resolve(acc["scope"]), **common)


def session_row(request: Request) -> dict:
    from app.core import auth
    row = auth.identify(request.cookies.get(auth.ACCESS_COOKIE))
    if not row:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    return row


def current_user(request: Request, x_demo_user: str | None = Header(default=None)) -> User:
    if config.AUTH_MODE == "demo":
        u = users().get((x_demo_user if config.PRO else None) or raw_default())  # Lite: 1 người dùng mặc định
        if u is None:
            raise HTTPException(status_code=401, detail="Chưa đăng nhập")
        return u
    row = session_row(request)
    if row["must_change_password"] and not is_preview_row(row):
        raise HTTPException(status_code=403, detail="Cần đổi mật khẩu trước khi sử dụng")
    return from_session(row)


def require_admin(request: Request) -> User:
    if config.AUTH_MODE == "demo":
        raise HTTPException(status_code=404, detail="Bản demo không có trang quản trị")
    u = current_user(request)
    if not u.is_admin:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên được thực hiện")
    return u


def raw_default() -> str:
    return yaml.safe_load(open(config.USERS_FILE, encoding="utf-8")).get("default_user", "ceo")


def require(user: User, module: str) -> None:
    if not user.can(module):
        raise HTTPException(status_code=403, detail="Bạn chưa được cấp quyền xem phân hệ này")

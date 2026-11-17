"""TỔNG QUAN điều hành – gom KPI và nhận định chính từ các phân hệ người dùng được xem."""
from __future__ import annotations

from app.core.periods import Period
from app.core.scope import User
from app.core.semantic import SemanticError
from app.modules.common import page

TITLE = "Tổng quan điều hành"
KEYWORDS: dict[str, list[str]] = {}
PICK = {  # phân hệ -> (KPI lấy lên tổng quan, khối lấy lên tổng quan)
    "sales": (["net_revenue", "plan_attainment"], ["trend"]),
    "pnl": (["npat", "net_margin"], ["bridge"]),
    "bs": (["cash", "ccc"], []),
    "cashflow": (["cfo", "fcf"], []),
    "inventory": (["inventory_value", "sku_below_safety"], []),
}


def build(period: Period, user: User, filters: dict) -> dict:
    from app.modules import REGISTRY
    kpis, blocks, insights, notes = [], [], [], []
    for mod, (kk, bb) in PICK.items():
        if not user.can(mod):
            continue
        try:
            d = REGISTRY[mod].build(period, user, filters)
        except SemanticError as e:
            notes.append(str(e))
            continue
        for k in d["kpis"]:
            if k["key"] in kk:
                kpis.append({**k, "module": mod})
        for b in d["blocks"]:
            if b["id"] in bb:
                blocks.append({**b, "id": f"{mod}.{b['id']}", "width": "half"})
        for i in d["insights"][:2]:
            insights.append({**i, "module": mod})
    order = {"bad": 0, "warn": 1, "good": 2, "info": 3}
    insights.sort(key=lambda i: order.get(i["level"], 9))
    return page("overview", TITLE, period, kpis, blocks, insights, notes=notes)

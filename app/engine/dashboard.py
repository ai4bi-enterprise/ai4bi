"""
Revenue Dashboard – dữ liệu cho màn hình Dashboard (Community Edition).

Mọi con số được tính qua CÙNG Compiler/Semantic Layer với chatbot (không viết SQL riêng),
nên số trên dashboard và số chatbot trả lời luôn khớp nhau.

Kỳ xem (mode):
  mtd : tháng đã chọn
  qtd : từ đầu quý đến hết tháng đã chọn
  ytd : từ tháng 1 đến hết tháng đã chọn
So sánh:
  - Kỳ trước : mtd -> tháng liền trước; qtd -> cùng số tháng đầu quý trước; ytd -> không có
               (bộ dữ liệu mẫu chỉ có năm 2025, nên KHÔNG có so sánh cùng kỳ năm trước).
  - KPI      : KPI chỉ có theo QUÝ. Với kỳ không trọn quý, KPI được phân bổ đều theo tháng
               (KPI quý ÷ 3 × số tháng) – quy ước được ghi rõ trên giao diện.
Mỗi thẻ có câu hỏi "Hỏi AI" đã được Router + Compiler kiểm chứng chạy được.
"""
from __future__ import annotations

import calendar
import threading
from datetime import date

from .compiler import Compiler, Intent, IntentError
from .rule_router import RuleRouter

# Màu nhận diện chi nhánh – khai báo DUY NHẤT tại đây, frontend đọc từ API
BRANCH_COLORS = {"Miền Bắc": "#1D4ED8", "Miền Trung": "#0D9488", "Miền Nam": "#DC2626"}  # vàng dành cho KPI
BRANCH_FALLBACK = "#64748B"
MODES = ("mtd", "qtd", "ytd")
NEAR_BAND = 0.95  # GẦN ĐẠT: 95–100% tiến độ


def _months(mode: str, m: int) -> list[int]:
    if mode == "mtd":
        return [m]
    if mode == "qtd":
        q0 = (m - 1) // 3 * 3 + 1
        return list(range(q0, m + 1))
    return list(range(1, m + 1))


def _prior_months(mode: str, m: int) -> list[int] | None:
    if mode == "mtd":
        return [m - 1] if m > 1 else None
    if mode == "qtd":
        cur = _months("qtd", m)
        if cur[0] == 1:
            return None
        return [x - 3 for x in cur]
    return None


def _label(mode: str, y: int, months: list[int]) -> str:
    if mode == "mtd":
        return f"Tháng {months[0]}/{y}"
    q = (months[0] - 1) // 3 + 1
    if mode == "qtd":
        return f"Quý {q}/{y}" if len(months) == 3 else f"QTD Q{q}/{y} (T{months[0]}–T{months[-1]})"
    return f"Năm {y}" if len(months) == 12 else f"YTD {y} (T1–T{months[-1]})"


def _pct(a, b):
    return None if a is None or not b else a / b


def _growth(a, b):
    return None if a is None or b in (None, 0) else a / b - 1


class DashboardBuilder:
    def __init__(self, compiler: Compiler, rules: RuleRouter, meta: dict):
        self.comp = compiler
        self.cat = compiler.cat
        self.rules = rules
        self.meta = meta
        self.data_end = compiler.data_end
        self._cache: dict = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- helpers
    def _run(self, metrics, dims=(), months=(), quarters=(), filters=None, order=None, limit=None, year=None):
        it = Intent(metrics=list(metrics), dimensions=list(dims), filters=dict(filters or {}),
                    year=year or self.data_end.year, quarters=list(quarters), months=list(months),
                    order=order, limit=limit)
        return self.comp.run(it)

    def _kpi(self, y: int, months: list[int], by_branch: bool) -> dict:
        """KPI phân bổ theo tháng: KPI quý ÷ 3 × số tháng của quý nằm trong kỳ."""
        r = self._run(["kpi_doanh_thu"], ["quy", "chi_nhanh"] if by_branch else ["quy"], year=y)
        per_q: dict = {}
        for row in r.rows:
            per_q[(row["quy"], row.get("chi_nhanh"))] = row["kpi_doanh_thu"] or 0
        out: dict = {}
        for (q, br), v in per_q.items():
            n = sum(1 for m in months if (m - 1) // 3 + 1 == q)
            if n:
                out[br] = out.get(br, 0) + v * n / 3
        return out

    def _ask(self, candidates: list[str]) -> str | None:
        """Trả về câu hỏi đầu tiên mà Router + Compiler xử lý được."""
        for q in candidates:
            try:
                self.comp.validate(self.rules.parse(q))
                return q
            except (IntentError, Exception):  # noqa: BLE001
                continue
        return None

    def _period_q(self, mode: str, months: list[int]) -> str:
        if mode == "mtd":
            return f"tháng {months[0]}"
        if mode == "qtd" and len(months) == 3:
            return f"quý {(months[0] - 1) // 3 + 1}"
        return " ".join(f"tháng {m}" for m in months) if mode == "qtd" else ""

    def _time_gone(self, y: int, months: list[int]) -> float:
        start = date(y, months[0], 1)
        end = date(y, months[-1], calendar.monthrange(y, months[-1])[1])
        if self.data_end >= end:
            return 1.0
        if self.data_end < start:
            return 0.0
        return ((self.data_end - start).days + 1) / ((end - start).days + 1)

    def _status(self, kpi_pct, time_gone) -> dict:
        if kpi_pct is None or not time_gone:
            return {"code": "none", "label": "Chưa có KPI"}
        ratio = kpi_pct / time_gone
        if ratio >= 1:
            return {"code": "good", "label": "Đạt tiến độ"}
        if ratio >= NEAR_BAND:
            return {"code": "warn", "label": "Gần đạt"}
        return {"code": "bad", "label": "Chưa đạt"}

    # ------------------------------------------------------------------ build
    def meta_info(self) -> dict:
        y = self.data_end.year
        return {"years": [y], "default_year": y, "default_month": self.data_end.month, "modes": list(MODES),
                "data_end": self.data_end.isoformat(), "company": self.meta.get("ten_cong_ty"),
                "branch_colors": BRANCH_COLORS, "branch_fallback": BRANCH_FALLBACK}

    def build(self, year: int | None, month: int | None, mode: str) -> dict:
        y = year or self.data_end.year
        m = month or self.data_end.month
        if mode not in MODES or not 1 <= m <= 12 or y != self.data_end.year:
            raise ValueError("Tham số kỳ không hợp lệ")
        key = (y, m, mode)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        data = self._build(y, m, mode)
        with self._lock:
            self._cache[key] = data
        return data

    def _build(self, y: int, m: int, mode: str) -> dict:
        months = _months(mode, m)
        pmonths = _prior_months(mode, m)
        label = _label(mode, y, months)
        plabel = _label(mode, y, pmonths) if pmonths else None
        tg = self._time_gone(y, months)
        pq = self._period_q(mode, months)
        cmp_word = {"mtd": "so với tháng trước", "qtd": "so với quý trước"}.get(mode, "")
        cmp_ok = mode == "mtd" or (mode == "qtd" and len(months) == 3)

        # ---- tổng công ty
        cur = self._run(["doanh_thu_thuan", "loi_nhuan_gop", "bien_loi_nhuan_gop", "loi_nhuan_thuan",
                         "thuc_thu"], months=months).totals or {}
        prv = (self._run(["doanh_thu_thuan", "bien_loi_nhuan_gop", "loi_nhuan_thuan", "thuc_thu"],
                         months=pmonths).totals or {}) if pmonths else {}
        od = self._run(["no_qua_han_90", "du_no"], months=months).totals or {}
        od_prev = (self._run(["no_qua_han_90"], months=pmonths).totals or {}) if pmonths else {}
        kpi_all = self._kpi(y, months, by_branch=False).get(None)
        rev = cur.get("doanh_thu_thuan")
        kpi_pct = _pct(rev, kpi_all)

        def card(key, label_, value, fmt, prior=None, hib=True, extra=None, ask=None):
            g = _growth(value, prior) if fmt == "money" else (None if value is None or prior is None else value - prior)
            return {"key": key, "label": label_, "value": value, "format": fmt, "prior": prior, "change": g,
                    "change_kind": "pct" if fmt == "money" else "pts", "higher_is_better": hib,
                    "extra": extra or [], "ask": ask}

        P = pq + (" " + cmp_word if cmp_ok and cmp_word else "")
        kpis = [
            card("doanh_thu_thuan", "Doanh thu thuần", rev, "money", prv.get("doanh_thu_thuan"),
                 extra=[{"label": "KPI", "value": kpi_all, "format": "money"},
                        {"label": "% KPI", "value": kpi_pct, "format": "pct"}],
                 ask=self._ask([f"Doanh thu thuần theo chi nhánh {P}".strip(), "Doanh thu thuần theo chi nhánh"])),
            card("bien_loi_nhuan_gop", "Biên lợi nhuận gộp", cur.get("bien_loi_nhuan_gop"), "pct",
                 prv.get("bien_loi_nhuan_gop"),
                 extra=[{"label": "LN gộp", "value": cur.get("loi_nhuan_gop"), "format": "money"}],
                 ask=self._ask([f"Biên lợi nhuận gộp theo chi nhánh {P}".strip(), "Biên lợi nhuận gộp theo chi nhánh"])),
            card("loi_nhuan_thuan", "Lợi nhuận thuần", cur.get("loi_nhuan_thuan"), "money", prv.get("loi_nhuan_thuan"),
                 extra=[{"label": "Biên LN thuần", "value": _pct(cur.get("loi_nhuan_thuan"), rev), "format": "pct"}],
                 ask=self._ask([f"Lợi nhuận thuần theo chi nhánh {P}".strip(), "Lợi nhuận thuần theo quý"])),
            card("thuc_thu", "Dòng tiền thực thu", cur.get("thuc_thu"), "money", prv.get("thuc_thu"),
                 extra=[{"label": "Thu/Doanh thu", "value": _pct(cur.get("thuc_thu"), rev), "format": "pct"}],
                 ask=self._ask([f"Dòng tiền thực thu theo loại khách hàng {P}".strip(),
                                "Dòng tiền thực thu theo loại khách hàng"])),
            card("no_qua_han_90", "Nợ quá hạn >90 ngày", od.get("no_qua_han_90"), "money", od_prev.get("no_qua_han_90"),
                 hib=False,
                 extra=[{"label": "% dư nợ", "value": _pct(od.get("no_qua_han_90"), od.get("du_no")), "format": "pct"}],
                 ask=self._ask([f"Nợ quá hạn >90 ngày theo loại khách hàng {pq}".strip(),
                                "Nợ quá hạn >90 ngày theo loại khách hàng"])),
        ]

        # ---- theo chi nhánh
        br = {x["chi_nhanh"]: x for x in self._run(["doanh_thu_thuan", "bien_loi_nhuan_gop"], ["chi_nhanh"],
                                                    months=months).rows}
        brp = {x["chi_nhanh"]: x for x in self._run(["doanh_thu_thuan"], ["chi_nhanh"], months=pmonths).rows} \
            if pmonths else {}
        bkpi = self._kpi(y, months, by_branch=True)
        order = self.cat.dimensions["chi_nhanh"].values
        branches = []
        for name in order:
            row = br.get(name, {})
            a = row.get("doanh_thu_thuan")
            k = bkpi.get(name)
            kp = _pct(a, k)
            branches.append({
                "name": name, "color": BRANCH_COLORS.get(name, BRANCH_FALLBACK),
                "actual": a, "prior": brp.get(name, {}).get("doanh_thu_thuan"), "kpi": k,
                "growth": _growth(a, brp.get(name, {}).get("doanh_thu_thuan")), "kpi_pct": kp,
                "margin": row.get("bien_loi_nhuan_gop"), "share": _pct(a, rev),
                "status": self._status(kp, tg),
                "ask": self._ask([f"Doanh thu thuần của {name} theo nhóm hàng {P}".strip(),
                                  f"Doanh thu thuần của {name} theo nhóm hàng"]),
            })

        # ---- xu hướng theo tháng (cả năm) & theo chi nhánh
        tr = self._run(["doanh_thu_thuan"], ["thang", "chi_nhanh"]).rows
        tcash = {x["thang"]: x["thuc_thu"] for x in self._run(["thuc_thu"], ["thang"]).rows}
        tot_m = {x["thang"]: x["doanh_thu_thuan"] for x in self._run(["doanh_thu_thuan"], ["thang"]).rows}
        last_m = self.data_end.month
        labels = list(range(1, last_m + 1))
        trend = {
            "labels": [f"T{i}" for i in labels], "highlight": months,
            "series": [{"name": n, "color": BRANCH_COLORS.get(n, BRANCH_FALLBACK),
                        "values": [next((x["doanh_thu_thuan"] for x in tr if x["thang"] == i and x["chi_nhanh"] == n), None)
                                   for i in labels]} for n in order],
            "total": [tot_m.get(i) for i in labels],
            "cash": [tcash.get(i) for i in labels],
            "ask": self._ask(["Doanh thu thuần theo tháng", "Doanh thu thuần theo quý"]),
        }

        # ---- nhóm hàng, top khách hàng, top sản phẩm
        grp = self._run(["doanh_thu_thuan", "bien_loi_nhuan_gop", "ty_le_chiet_khau"], ["nhom_hang"], months=months)
        groups = [{"name": x["nhom_hang"], "actual": x["doanh_thu_thuan"], "margin": x["bien_loi_nhuan_gop"],
                   "discount": x["ty_le_chiet_khau"], "share": _pct(x["doanh_thu_thuan"], rev)} for x in grp.rows]

        def top(dim, n=10):
            r = self._run(["doanh_thu_thuan"], [dim], months=months, order="desc", limit=n)
            return [{"name": x[dim], "value": x["doanh_thu_thuan"], "share": _pct(x["doanh_thu_thuan"], rev)}
                    for x in r.rows]

        return {
            "period": {"year": y, "month": m, "mode": mode, "months": months, "label": label,
                       "prior_label": plabel, "time_gone": tg, "kpi_note": None if mode != "mtd" and len(months) % 3 == 0
                       and (mode == "ytd" or len(months) == 3) else "KPI phân bổ đều theo tháng từ KPI quý"},
            "header": {"data_end": self.data_end.isoformat(), "company": self.meta.get("ten_cong_ty"),
                       "time_gone": tg},
            "total_status": self._status(kpi_pct, tg),
            "kpis": kpis,
            "branches": branches,
            "trend": trend,
            "groups": groups,
            "groups_ask": self._ask([f"Biên lợi nhuận gộp theo nhóm hàng {P}".strip(),
                                     "Biên lợi nhuận gộp theo nhóm hàng"]),
            "top_customers": top("khach_hang"),
            "top_customers_ask": self._ask([f"Top 10 khách hàng có doanh thu cao nhất {pq}".strip(),
                                            "Top 10 khách hàng có doanh thu cao nhất"]),
            "top_products": top("san_pham"),
            "top_products_ask": self._ask([f"Top 10 sản phẩm có doanh thu cao nhất {pq}".strip(),
                                           "Top 10 sản phẩm có doanh thu cao nhất"]),
        }

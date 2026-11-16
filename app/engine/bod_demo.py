"""
BOD Deep-Dive – KỊCH BẢN DEMO (Community Edition).

Bản Community KHÔNG có Multi-step Planner / Root-Cause Engine. Thay vào đó, 2 kịch bản
phân tích mẫu được dựng sẵn; MỌI con số vẫn được truy vấn trực tiếp từ database mẫu
(không hard-code), để người xem có thể đối soát bằng các câu hỏi thường.
Bản Pro/Enterprise tự lập kế hoạch truy vấn đa bước cho câu hỏi bất kỳ trên dữ liệu thật.

Bố cục trả lời thống nhất với câu hỏi thường: Nhận xét · Đánh giá · Kết luận · Đề xuất · Gợi ý.
"""
from __future__ import annotations

import re

from .db import Database
from .formatter import _vn, change_text, fmt, tone
from .text import normalize

UPSELL_MD = (
    "Tính năng **tự động bóc tách đa chiều và phân tích nguyên nhân gốc rễ (Root-Cause Analysis)** "
    "cho câu hỏi bất kỳ của BOD thuộc phiên bản **AI4BI Enterprise**. Bản Community chỉ trình diễn "
    "kịch bản dựng sẵn trên bộ dữ liệu mẫu."
)

SCENARIOS = {
    "cash_paradox": "Tại sao doanh thu Quý 3 vượt KPI 18% nhưng dòng tiền thực thu lại sụt giảm?",
    "margin_erosion": "Nhóm hàng và khu vực nào đang làm bào mòn biên lợi nhuận của toàn công ty?",
}

# Câu hỏi gợi ý cho từng kịch bản – được kiểm chứng chạy được trong tests/test_ai4bi.py
FOLLOWUPS = {
    "cash_paradox": [
        "Tỷ lệ chiết khấu Nhóm A Miền Nam theo tháng",
        "Dòng tiền thực thu theo loại khách hàng Quý 3 so với quý trước",
        "Nợ quá hạn trên 90 ngày theo loại khách hàng theo quý",
        "Biên lợi nhuận gộp theo chi nhánh Quý 3 so với quý trước",
    ],
    "margin_erosion": [
        "Biên lợi nhuận gộp của Miền Nam theo nhóm hàng quý 3",
        "Tỷ lệ chiết khấu theo chi nhánh Quý 3 so với quý trước",
        "Doanh thu thuần của Nhóm A - Đồ uống theo chi nhánh quý 3",
        "Lợi nhuận thuần theo quý",
    ],
}


def pick_scenario(question: str) -> str | None:
    n = normalize(question)
    if re.search(r"bao mon|an mon|bien loi nhuan|lam giam loi nhuan|loi nhuan gop giam|margin", n):
        return "margin_erosion"
    if re.search(r"dong tien|thuc thu|tien mat|thu tien|vuot kpi|loi nhuan .*giam|quy 3|q3|cong no", n):
        return "cash_paradox"
    return None


def _insights(status: str, sections: dict[str, list], suggestions: list[str]) -> dict:
    labels = {"good": "Tích cực", "neutral": "Ổn định", "warn": "Cần theo dõi", "bad": "Cảnh báo"}
    titles = {"nhan_xet": "Nhận xét", "danh_gia": "Đánh giá", "ket_luan": "Kết luận", "de_xuat": "Đề xuất"}
    return {"type": "insights", "status": status, "status_label": labels[status],
            "basis": "Kịch bản demo · số liệu truy vấn trực tiếp từ dữ liệu mẫu",
            "sections": [{"key": k, "title": titles[k], "items": v} for k, v in sections.items() if v],
            "suggestions": suggestions}


class BodDemo:
    def __init__(self, db: Database, contact_url: str = "#", gm_warn: float = 0.19):
        self.db = db
        self.contact_url = contact_url
        self.gm_warn = gm_warn          # ngưỡng cảnh báo biên LN gộp, lấy từ Semantic Layer
        self.sql: list[str] = []

    # ------------------------------------------------------------ helpers
    def _q(self, sql: str, params: tuple = ()) -> list[tuple]:
        self.sql.append(sql.strip())
        return self.db.query(sql, params)[1]

    def _overdue90(self, asof: str, by: str | None = None):
        sql = f"""
SELECT {by + ', ' if by else ''}SUM(so_tien - CASE WHEN ngay_thanh_toan <= ? THEN so_tien_da_thu ELSE 0 END) AS no_qua_han_90
FROM v_ar
WHERE ngay_chung_tu <= ? AND julianday(?) - julianday(han_thanh_toan) > 90
  AND so_tien - CASE WHEN ngay_thanh_toan <= ? THEN so_tien_da_thu ELSE 0 END <> 0
{('GROUP BY ' + by) if by else ''}"""
        rows = self._q(sql, (asof, asof, asof, asof))
        return rows if by else (rows[0][0] or 0)

    def _callout(self) -> dict:
        return {"type": "callout", "variant": "pro", "title": "AI4BI Enterprise", "md": UPSELL_MD,
                "cta": {"label": "Xem chi tiết / Liên hệ tích hợp vào dữ liệu doanh nghiệp của bạn",
                        "url": self.contact_url}}

    def run(self, question: str) -> dict:
        self.sql = []
        key = pick_scenario(question)
        if key == "cash_paradox":
            blocks = self.cash_paradox()
        elif key == "margin_erosion":
            blocks = self.margin_erosion()
        else:
            blocks = [
                {"type": "text", "md": "**Chế độ Phân tích Quản trị (BOD Deep-Dive)**\n\n"
                 "Câu hỏi của anh/chị cần bộ máy lập kế hoạch phân tích đa bước (Multi-step Planner) "
                 "của bản Enterprise. Bản Community đang có sẵn 2 kịch bản demo chạy trên dữ liệu mẫu:"},
                {"type": "suggestions", "items": list(SCENARIOS.values())},
                self._callout(),
            ]
        return {"scenario": key, "blocks": blocks, "sql": self.sql}

    # ---------------------------------------------------- scenario 1
    def cash_paradox(self) -> list[dict]:
        rev = dict(self._q("SELECT quy, SUM(doanh_thu_thuan) FROM v_sales WHERE nam=2025 GROUP BY quy"))
        gp = dict(self._q("SELECT quy, SUM(doanh_thu_thuan-gia_von) FROM v_sales WHERE nam=2025 GROUP BY quy"))
        opex = dict(self._q("SELECT quy, SUM(so_tien) FROM v_expense WHERE nam=2025 GROUP BY quy"))
        cash = dict(self._q("SELECT quy, SUM(so_tien_thu) FROM v_cash WHERE nam=2025 GROUP BY quy"))
        kpi = dict(self._q("SELECT quy, SUM(kpi) FROM v_kpi WHERE nam=2025 GROUP BY quy"))
        np_ = {q: gp[q] - opex[q] for q in gp}
        od2, od3 = self._overdue90("2025-06-30"), self._overdue90("2025-09-30")

        T = lambda v: fmt(v, "money", (1e9, "tỷ"))  # noqa: E731
        P = lambda v: fmt(v, "pct")  # noqa: E731
        surface = [
            ["Doanh thu thuần", T(rev[2]), T(rev[3]), change_text(rev[3], rev[2], "money")],
            ["Tỷ lệ đạt KPI doanh thu", P(rev[2] / kpi[2]), P(rev[3] / kpi[3]), change_text(rev[3] / kpi[3], rev[2] / kpi[2], "pct")],
            ["Lợi nhuận gộp", T(gp[2]), T(gp[3]), change_text(gp[3], gp[2], "money")],
            ["Biên lợi nhuận gộp", P(gp[2] / rev[2]), P(gp[3] / rev[3]), change_text(gp[3] / rev[3], gp[2] / rev[2], "pct")],
            ["Chi phí bán hàng & quản lý", T(opex[2]), T(opex[3]), change_text(opex[3], opex[2], "money")],
            ["Lợi nhuận thuần", T(np_[2]), T(np_[3]), change_text(np_[3], np_[2], "money")],
            ["Dòng tiền thực thu", T(cash[2]), T(cash[3]), change_text(cash[3], cash[2], "money")],
            ["Nợ quá hạn >90 ngày (cuối kỳ)", T(od2), T(od3), change_text(od3, od2, "money") if od2 else "Mới phát sinh"],
        ]
        surface_tones = [["", "", "", tone(c, p, hib)] for (p, c, hib) in [
            (rev[2], rev[3], True), (rev[2] / kpi[2], rev[3] / kpi[3], True), (gp[2], gp[3], True),
            (gp[2] / rev[2], gp[3] / rev[3], True), (opex[2], opex[3], False), (np_[2], np_[3], True),
            (cash[2], cash[3], True), (od2, od3, False)]]

        # Nguyên nhân 1: chiết khấu vượt trần
        disc = self._q("""
SELECT chi_nhanh, nhom_hang, MAX(tran_chiet_khau) AS tran,
       SUM(CASE WHEN quy=2 THEN chiet_khau END)*1.0/SUM(CASE WHEN quy=2 THEN thanh_tien_gop END) AS ck_q2,
       SUM(CASE WHEN quy=3 THEN chiet_khau END)*1.0/SUM(CASE WHEN quy=3 THEN thanh_tien_gop END) AS ck_q3,
       SUM(CASE WHEN quy=2 THEN doanh_thu_thuan-gia_von END)*1.0/SUM(CASE WHEN quy=2 THEN doanh_thu_thuan END) AS gm_q2,
       SUM(CASE WHEN quy=3 THEN doanh_thu_thuan-gia_von END)*1.0/SUM(CASE WHEN quy=3 THEN doanh_thu_thuan END) AS gm_q3,
       SUM(CASE WHEN quy=2 THEN doanh_thu_thuan END) AS dt_q2,
       SUM(CASE WHEN quy=3 THEN doanh_thu_thuan END) AS dt_q3
FROM v_sales WHERE nam=2025 AND quy IN (2,3)
GROUP BY chi_nhanh, nhom_hang ORDER BY ck_q3 DESC""")
        worst = disc[0]
        d_rev_total = rev[3] - rev[2]
        d_rev_worst = worst[8] - worst[7]
        over_cap = [r for r in disc if r[4] > r[2]]
        dealer_disc = self._q("""
SELECT loai_khach_hang, SUM(chiet_khau)*1.0/SUM(thanh_tien_gop), SUM(doanh_thu_thuan)
FROM v_sales WHERE nam=2025 AND quy=3 AND chi_nhanh=? AND nhom_hang=?
GROUP BY loai_khach_hang ORDER BY 2 DESC""", (worst[0], worst[1]))

        # Nguyên nhân 2: đại lý cấp 2
        od_type2 = dict(self._overdue90("2025-06-30", "loai_khach_hang"))
        od_type3 = dict(self._overdue90("2025-09-30", "loai_khach_hang"))
        cash_type = self._q("""
SELECT loai_khach_hang, SUM(CASE WHEN quy=2 THEN so_tien_thu END), SUM(CASE WHEN quy=3 THEN so_tien_thu END)
FROM v_cash WHERE nam=2025 AND quy IN (2,3) GROUP BY loai_khach_hang""")
        d2 = "Đại lý cấp 2"
        c2 = {r[0]: (r[1] or 0, r[2] or 0) for r in cash_type}
        d_cash_total = cash[3] - cash[2]
        d_cash_d2 = c2[d2][1] - c2[d2][0]
        top_d2 = self._q("""
SELECT khach_hang, chi_nhanh,
       SUM(so_tien - CASE WHEN ngay_thanh_toan <= '2025-09-30' THEN so_tien_da_thu ELSE 0 END) AS no90
FROM v_ar
WHERE ngay_chung_tu <= '2025-09-30' AND loai_khach_hang = ?
  AND julianday('2025-09-30') - julianday(han_thanh_toan) > 90
GROUP BY khach_hang, chi_nhanh HAVING no90 > 0 ORDER BY no90 DESC LIMIT 5""", (d2,))
        promo = self._q("""
SELECT SUM(CASE WHEN quy=2 THEN so_tien END), SUM(CASE WHEN quy=3 THEN so_tien END)
FROM v_expense WHERE nam=2025 AND chi_nhanh=? AND loai_chi_phi='Khuyến mại & trưng bày'""", (worst[0],))[0]
        q4 = self._q("""SELECT SUM(doanh_thu_thuan) FROM v_sales WHERE nam=2025 AND quy=4 AND chi_nhanh=? AND nhom_hang=?""",
                     (worst[0], worst[1]))[0][0]

        head = ("_Kịch bản demo — số liệu được truy vấn trực tiếp từ dữ liệu mẫu._\n\n"
                f"**Bề nổi vs Bề chìm Q2 → Q3/2025:** doanh thu thuần **{change_text(rev[3], rev[2], 'money')}**, "
                f"lợi nhuận thuần **{change_text(np_[3], np_[2], 'money')}**, "
                f"dòng tiền thực thu **{change_text(cash[3], cash[2], 'money')}**.")
        cash_line = (f"Thực thu từ {d2}: {T(c2[d2][0])} (Q2) → **{T(c2[d2][1])}** (Q3), giảm **{T(-d_cash_d2)}** — "
                     f"lớn hơn cả mức giảm chung {T(-d_cash_total)} của toàn công ty."
                     if d_cash_d2 <= d_cash_total else
                     f"Thực thu từ {d2}: {T(c2[d2][0])} (Q2) → **{T(c2[d2][1])}** (Q3), giải thích "
                     f"**{_vn(d_cash_d2 / d_cash_total * 100, 0)}%** mức sụt giảm dòng tiền.")
        ins = _insights("bad", {
            "nhan_xet": [
                {"text": f"Doanh thu thuần Q3 đạt **{T(rev[3])}** ({change_text(rev[3], rev[2], 'money')} so với Q2), "
                         f"đạt **{P(rev[3] / kpi[3])} KPI**."},
                {"text": f"Riêng **{worst[0]} × {worst[1]}** đóng góp **{T(d_rev_worst)}** trong mức tăng {T(d_rev_total)} "
                         f"(≈ {_vn(d_rev_worst / d_rev_total * 100, 0)}%); chiết khấu Q3 theo loại khách: "
                         + "; ".join(f"{r[0]} {P(r[1])}" for r in dealer_disc) + "."},
                {"text": f"Chi phí khuyến mại & trưng bày {worst[0]}: {T(promo[0])} (Q2) → **{T(promo[1])}** (Q3)."},
                {"text": cash_line},
                {"text": f"Doanh thu {worst[1]} tại {worst[0]} Q4 chỉ còn {T(q4)} so với {T(worst[8])} ở Q3."},
            ],
            "danh_gia": [
                {"sev": "bad", "label": "Cảnh báo",
                 "text": f"Tỷ lệ chiết khấu {worst[0]} × {worst[1]} Q3 là {P(worst[4])}, vượt trần chính sách "
                         f"{P(worst[2])} ({len(over_cap)} tổ hợp vượt trần); biên LN gộp {P(worst[5])} → {P(worst[6])}."},
                {"sev": "bad", "label": "Cảnh báo",
                 "text": f"Nợ quá hạn >90 ngày của {d2} tăng từ {T(od_type2.get(d2, 0))} (30/06) lên "
                         f"{T(od_type3.get(d2, 0))} (30/09); toàn công ty {T(od2)} → {T(od3)}."},
                {"sev": "warn", "label": "Cần theo dõi",
                 "text": f"Lợi nhuận thuần giảm {change_text(np_[3], np_[2], 'money')} dù doanh thu tăng "
                         f"{change_text(rev[3], rev[2], 'money')} — tăng trưởng không chuyển hóa thành lợi nhuận."},
                {"sev": "warn", "label": "Cần theo dõi",
                 "text": f"Doanh thu {worst[1]} tại {worst[0]} Q4 sụt mạnh so với Q3 — dấu hiệu dồn hàng vào kênh."},
            ],
            "ket_luan": [{"text": "Tổng thể: **cảnh báo**. Tăng trưởng Q3 là tăng trưởng “mua bằng chiết khấu và công nợ”: "
                                  f"doanh thu vượt KPI nhờ chương trình chiết khấu vượt trần tại {worst[0]}, trong khi "
                                  f"{d2} gần như ngừng thanh toán khiến lợi nhuận và dòng tiền cùng giảm."}],
            "de_xuat": [
                {"text": f"Dừng ngay chương trình chiết khấu vượt trần tại {worst[0]}; mọi mức chiết khấu vượt trần phải được CFO phê duyệt trước."},
                {"text": f"Khoanh nợ {d2}: tạm ngừng giao hàng với khách có nợ >90 ngày, lập kế hoạch thu hồi "
                         f"{T(od_type3.get(d2, 0))} và đánh giá dự phòng nợ khó đòi."},
                {"text": "Đổi thước đo KPI bán hàng từ “doanh thu ghi nhận” sang “doanh thu đã thu tiền” và “lợi nhuận gộp”."},
            ],
        }, FOLLOWUPS["cash_paradox"])
        return [
            {"type": "text", "md": head},
            {"type": "table", "title": "Bề nổi vs Bề chìm (Q2 → Q3/2025)",
             "columns": [{"key": "a", "label": "Chỉ số", "align": "left"},
                         {"key": "b", "label": "Quý 2/2025", "align": "right"},
                         {"key": "c", "label": "Quý 3/2025", "align": "right"},
                         {"key": "d", "label": "Biến động", "align": "right"}],
             "rows": surface, "highlight_rows": [5, 6, 7], "tones": surface_tones},
            {"type": "chart", "kind": "bar", "title": "Doanh thu thuần vs Dòng tiền thực thu theo quý (tỷ)",
             "labels": ["Q1", "Q2", "Q3", "Q4"],
             "series": [{"name": "Doanh thu thuần", "role": "main", "values": [round(rev[q] / 1e9, 2) for q in (1, 2, 3, 4)]},
                        {"name": "Dòng tiền thực thu", "role": "secondary", "values": [round(cash[q] / 1e9, 2) for q in (1, 2, 3, 4)]}]},
            {"type": "table", "title": "Tỷ lệ chiết khấu & biên LN gộp theo Chi nhánh × Nhóm hàng (Top 5 theo CK Q3)",
             "columns": [{"key": "a", "label": "Chi nhánh", "align": "left"}, {"key": "b", "label": "Nhóm hàng", "align": "left"},
                         {"key": "c", "label": "Trần CK", "align": "right"}, {"key": "d", "label": "CK Q2", "align": "right"},
                         {"key": "e", "label": "CK Q3", "align": "right"}, {"key": "f", "label": "Biên LN gộp Q2", "align": "right"},
                         {"key": "g", "label": "Biên LN gộp Q3", "align": "right"}],
             "rows": [[r[0], r[1], P(r[2]), P(r[3]), P(r[4]), P(r[5]), P(r[6])] for r in disc[:5]],
             "highlight_rows": [i for i, r in enumerate(disc[:5]) if r[4] > r[2]]},
            {"type": "table", "title": f"Top 5 {d2.lower()} có nợ quá hạn >90 ngày lớn nhất tại 30/09/2025",
             "columns": [{"key": "a", "label": "Khách hàng", "align": "left"},
                         {"key": "b", "label": "Chi nhánh", "align": "left"},
                         {"key": "c", "label": "Nợ quá hạn >90 ngày (tỷ)", "align": "right"}],
             "rows": [[r[0], r[1], fmt(r[2], "money", (1e9, "tỷ"), with_unit=False)] for r in top_d2]},
            ins,
            self._callout(),
        ]

    # ---------------------------------------------------- scenario 2
    def margin_erosion(self) -> list[dict]:
        rows = self._q("""
SELECT chi_nhanh, nhom_hang,
       SUM(CASE WHEN quy=2 THEN doanh_thu_thuan END) AS dt2, SUM(CASE WHEN quy=3 THEN doanh_thu_thuan END) AS dt3,
       SUM(CASE WHEN quy=2 THEN doanh_thu_thuan-gia_von END) AS gp2, SUM(CASE WHEN quy=3 THEN doanh_thu_thuan-gia_von END) AS gp3
FROM v_sales WHERE nam=2025 AND quy IN (2,3) GROUP BY chi_nhanh, nhom_hang""")
        R2 = sum(r[2] for r in rows); R3 = sum(r[3] for r in rows)
        G2 = sum(r[4] for r in rows); G3 = sum(r[5] for r in rows)
        gm2, gm3 = G2 / R2, G3 / R3
        cells = []
        rate_eff = mix_eff = 0.0
        for c, g, dt2, dt3, gp2, gp3 in rows:
            m2, m3 = gp2 / dt2, gp3 / dt3
            w2, w3 = dt2 / R2, dt3 / R3
            rate_eff += w3 * (m3 - m2)
            mix_eff += (w3 - w2) * m2
            cells.append((c, g, dt3, m2, m3, dt3 * (m2 - m3)))
        cells.sort(key=lambda x: -x[5])
        T = lambda v: fmt(v, "money", (1e9, "tỷ"))  # noqa: E731
        P = lambda v: fmt(v, "pct")  # noqa: E731
        top = cells[0]
        lost_total = sum(x[5] for x in cells if x[5] > 0)
        others = [x for x in cells[1:] if x[5] > 0]
        head = ("_Kịch bản demo — số liệu được truy vấn trực tiếp từ dữ liệu mẫu._\n\n"
                f"**Biên lợi nhuận gộp toàn công ty:** {P(gm2)} (Q2) → **{P(gm3)}** (Q3), "
                f"{change_text(gm3, gm2, 'pct')}.")
        ins = _insights("bad", {
            "nhan_xet": [
                {"text": f"Hiệu ứng **biên trong từng tổ hợp** (rate): {_vn(rate_eff * 100, 2)} điểm %; hiệu ứng "
                         f"**cơ cấu** (mix): {_vn(mix_eff * 100, 2)} điểm % — suy giảm đến chủ yếu từ biên, không phải cơ cấu."},
                {"text": f"Điểm bào mòn lớn nhất: **{top[0]} × {top[1]}** — biên {P(top[3])} → **{P(top[4])}**, "
                         f"làm mất **{T(top[5])}** lợi nhuận gộp so với biên Q2 "
                         f"(≈ {_vn(top[5] / lost_total * 100, 0)}% tổng phần bào mòn)."},
            ] + ([{"text": f"Các tổ hợp còn lại gần như giữ nguyên biên (tổng phần bào mòn còn lại chỉ "
                           f"{T(sum(x[5] for x in others))})."}]
                 if sum(x[5] for x in others) < 0.2 * lost_total else
                 [{"text": f"Phần bào mòn còn lại {T(sum(x[5] for x in others))} phân tán ở {len(others)} tổ hợp khác."}]),
            "danh_gia": [
                {"sev": "bad", "label": "Cảnh báo",
                 "text": f"Biên LN gộp toàn công ty Q3 {P(gm3)} "
                         f"{'dưới' if gm3 < self.gm_warn else 'trên'} ngưỡng cảnh báo {P(self.gm_warn)} (Q2: {P(gm2)})."},
                {"sev": "bad", "label": "Cảnh báo",
                 "text": f"{top[0]} × {top[1]}: biên {P(top[4])}, thấp hơn rất xa mặt bằng chung — nguồn bào mòn tập trung."},
            ],
            "ket_luan": [{"text": f"Tổng thể: **cảnh báo**. Bào mòn biên lợi nhuận không lan rộng mà tập trung gần như "
                                  f"hoàn toàn tại {top[0]} × {top[1]}; xử lý đúng điểm này sẽ khôi phục phần lớn biên công ty."}],
            "de_xuat": [
                {"text": f"Rà soát chương trình bán hàng của {top[0]} cho {top[1]}: so sánh sản lượng tăng thêm với phần biên bị mất."},
                {"text": "Thiết lập cảnh báo tự động khi tỷ lệ chiết khấu của bất kỳ chi nhánh × nhóm hàng nào vượt trần chính sách."},
                {"text": "Báo cáo biên lợi nhuận theo tuần cho Giám đốc chi nhánh thay vì chỉ báo cáo doanh thu."},
            ],
        }, FOLLOWUPS["margin_erosion"])
        return [
            {"type": "text", "md": head},
            {"type": "table", "title": "Top 5 tổ hợp Chi nhánh × Nhóm hàng bào mòn lợi nhuận gộp (Q3 so với biên Q2)",
             "columns": [{"key": "a", "label": "Chi nhánh", "align": "left"}, {"key": "b", "label": "Nhóm hàng", "align": "left"},
                         {"key": "c", "label": "Doanh thu Q3 (tỷ)", "align": "right"}, {"key": "d", "label": "Biên Q2", "align": "right"},
                         {"key": "e", "label": "Biên Q3", "align": "right"}, {"key": "f", "label": "LN gộp bị bào mòn (tỷ)", "align": "right"}],
             "rows": [[x[0], x[1], fmt(x[2], "money", (1e9, "tỷ"), False), P(x[3]), P(x[4]),
                       fmt(x[5], "money", (1e9, "tỷ"), False)] for x in cells[:5]],
             "highlight_rows": [0]},
            {"type": "chart", "kind": "bar", "title": "LN gộp bị bào mòn theo tổ hợp (tỷ)",
             "labels": [f"{x[0]} · {x[1].split(' - ')[0]}" for x in cells[:6]],
             "series": [{"name": "LN gộp bị bào mòn", "role": "main", "values": [round(x[5] / 1e9, 3) for x in cells[:6]]}]},
            ins,
            self._callout(),
        ]

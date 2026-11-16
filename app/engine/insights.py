"""
Insight Engine – sinh 5 mục phân tích cho mỗi câu trả lời số liệu:

    Nhận xét  : sự kiện rút ra từ kết quả (cao/thấp nhất, biến động, đóng góp, xu hướng…)
    Đánh giá  : chấm mức Tích cực / Ổn định / Cần theo dõi / Cảnh báo theo NGƯỠNG trong YAML
    Kết luận  : một câu tổng hợp trạng thái chung và điểm chính
    Đề xuất   : hành động lấy từ khối `actions` của chỉ số trong YAML (không tự bịa)
    Gợi ý     : câu hỏi tiếp theo – mỗi câu đã được Router + Compiler kiểm chứng chạy được

Nguyên tắc Zero-Hallucination:
  - 100% tất định (deterministic), KHÔNG dùng LLM. Mọi con số lấy từ QueryResult hoặc
    từ truy vấn bổ sung qua chính Compiler (SQL hiển thị trong "Xem SQL").
  - Ngưỡng đánh giá & nội dung đề xuất do doanh nghiệp khai báo trong semantic_models/*.yaml.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .catalog import Catalog, Metric
from .compiler import TIME_DIMS, Compiler, Intent, IntentError, QueryResult
from .formatter import _vn, fmt
from .rule_router import RuleRouter
from .text import normalize

SEV = {"good": 0, "neutral": 1, "warn": 2, "bad": 3}
STATUS_LABEL = {"good": "Tích cực", "neutral": "Ổn định", "warn": "Cần theo dõi", "bad": "Cảnh báo"}
MAX_OBS, MAX_FINDINGS, MAX_ACTIONS, MAX_SUGGEST = 6, 4, 3, 4


@dataclass
class Finding:
    sev: str
    text: str
    metric: str
    row: str | None = None     # nhãn đối tượng liên quan (chi nhánh, khách hàng…)
    kind: str = "level"        # level | change | rows | divergence | concentration | streak


@dataclass
class Ctx:
    """Kết quả + dữ liệu kỳ trước (nếu có) đã gióng hàng theo khóa chiều."""
    r: QueryResult
    prev_rows: dict = field(default_factory=dict)    # key -> {metric: prev}
    prev_tot: dict = field(default_factory=dict)
    prev_label: str | None = None
    extra_sql: list = field(default_factory=list)


def _pct_pts(x: float) -> str:
    return _vn(abs(x) * 100, 1) + " điểm %"


class InsightEngine:
    def __init__(self, catalog: Catalog, compiler: Compiler, rules: RuleRouter):
        self.cat = catalog
        self.comp = compiler
        self.rules = rules
        self._finds: list[Finding] = []

    # ================================================================ public
    def build(self, r: QueryResult, question: str = "") -> tuple[dict | None, list[str]]:
        if not r.rows:
            return None, []
        ctx = self._context(r)
        obs: list[str] = []
        finds: list[Finding] = []
        metrics = r.metrics[:2]
        tdims = [d for d in r.dims if d in TIME_DIMS]
        cdims = [d for d in r.dims if d not in TIME_DIMS]

        for i, m in enumerate(metrics):
            if not tdims:
                self._totals(ctx, m, obs, finds)
                if cdims and len(r.rows) >= 2:
                    self._rows(ctx, m, obs, finds, first=(i == 0))
            elif len(tdims) == 1 and not cdims:
                self._series(ctx, m, tdims[0], obs, finds)
            elif len(tdims) == 1 and len(cdims) == 1:
                self._series_by_cat(ctx, m, tdims[0], cdims[0], obs, finds)
        if len(metrics) == 2 and not tdims:
            self._divergence(ctx, metrics[0], metrics[1], obs, finds)

        self._finds = finds
        status = max((f.sev for f in finds), key=lambda s: SEV[s], default=None)
        sections = [
            {"key": "nhan_xet", "title": "Nhận xét", "items": [{"text": t} for t in obs[:MAX_OBS]]},
            {"key": "danh_gia", "title": "Đánh giá", "items": self._assessment(finds)},
            {"key": "ket_luan", "title": "Kết luận", "items": [{"text": self._conclusion(ctx, finds, status)}]},
            {"key": "de_xuat", "title": "Đề xuất", "items": [{"text": t} for t in self._actions(ctx, finds, status)]},
        ]
        sugg = self._suggestions(ctx, question)
        block = {
            "type": "insights",
            "status": status or "none",
            "status_label": STATUS_LABEL.get(status, "Chưa đủ cơ sở đánh giá"),
            "basis": self._basis(ctx),
            "sections": [s for s in sections if s["items"]],
            "suggestions": sugg,
        }
        return block, ctx.extra_sql

    # ============================================================== context
    def _context(self, r: QueryResult) -> Ctx:
        ctx = Ctx(r=r)
        it = r.intent
        keyf = lambda row: tuple(row.get(d) for d in r.dims)  # noqa: E731
        if it.compare == "prev" and r.prev_label:
            ctx.prev_label = self._mid(r.prev_label)
            ctx.prev_tot = dict(r.prev_totals or {})
            ctx.prev_rows = {keyf(x): {m: x.get(f"{m}__prev") for m in r.metrics} for x in r.rows}
            return ctx
        if any(d in TIME_DIMS for d in r.dims):
            return ctx
        snapshot_only = all(self.cat.is_snapshot(m) for m in r.metrics)
        eligible = self.comp.prev_period(it) is not None or (snapshot_only and not it.quarters and not it.months)
        if not eligible:
            return ctx
        try:
            raw = copy.deepcopy(it.to_dict())
            raw["compare"] = "prev"
            raw["limit"] = None
            raw["order"] = None
            pit = Intent(**raw)
            pr = self.comp.run(pit)
        except Exception:  # noqa: BLE001  (không để phần phân tích làm hỏng câu trả lời)
            return ctx
        if not pr.prev_label:
            return ctx
        ctx.prev_label = self._mid(pr.prev_label)
        ctx.prev_tot = dict(pr.prev_totals or {})
        ctx.prev_rows = {keyf(x): {m: x.get(f"{m}__prev") for m in r.metrics} for x in pr.rows}
        ctx.extra_sql = [s for s in pr.sql if s not in r.sql]
        return ctx

    @staticmethod
    def _lc(label: str) -> str:
        """Viết thường chữ đầu nhưng giữ nguyên viết tắt (KPI, CK…)."""
        return label[0].lower() + label[1:] if label else label

    @staticmethod
    def _mid(label: str | None) -> str | None:
        return label[0].lower() + label[1:] if label and label.startswith("Ngày chốt") else label

    def _basis(self, ctx: Ctx) -> str:
        parts = ["Phân tích tự động theo quy tắc (không dùng LLM)"]
        if ctx.prev_label:
            parts.append(f"so sánh với {ctx.prev_label}")
        if any(self.cat.metrics[m].assess for m in ctx.r.metrics):
            parts.append("ngưỡng đánh giá khai báo trong Semantic Layer")
        return " · ".join(parts)

    # ============================================================== helpers
    def _m(self, m: str) -> Metric:
        return self.cat.metrics[m]

    def _row_label(self, row: dict, dims: list[str]) -> str:
        out = []
        for d in dims:
            v = row.get(d)
            out.append(f"Quý {v}" if d == "quy" else f"Tháng {v}" if d == "thang" else str(v))
        return " · ".join(out)

    def _f(self, m: str, v) -> str:
        return fmt(v, self._m(m).fmt)

    def _level(self, m: str, v) -> str | None:
        mm = self._m(m)
        if not mm.assess or v is None:
            return None
        g, w = mm.assess["good"], mm.assess["warn"]
        if mm.higher_is_better:
            return "good" if v >= g else "warn" if v >= w else "bad"
        return "good" if v <= g else "warn" if v <= w else "bad"

    def _level_phrase(self, m: str, sev: str) -> str:
        mm = self._m(m)
        g, w = (self._f(m, mm.assess["good"]), self._f(m, mm.assess["warn"]))
        if mm.higher_is_better:
            return {"good": f"đạt ngưỡng tốt (≥ {g})", "warn": f"dưới ngưỡng tốt {g}",
                    "bad": f"dưới ngưỡng cảnh báo {w}"}[sev]
        return {"good": f"trong ngưỡng tốt (≤ {g})", "warn": f"vượt ngưỡng tốt {g}",
                "bad": f"vượt ngưỡng cảnh báo {w}"}[sev]

    def _signed(self, m: str, cur, prev) -> float | None:
        """Biến động đã quy về chiều 'tốt' (dương = tốt hơn)."""
        if cur is None or prev is None:
            return None
        mm = self._m(m)
        if mm.fmt == "pct":
            d = cur - prev
        else:
            if prev == 0:
                return None
            d = (cur - prev) / abs(prev)
        return d if mm.higher_is_better else -d

    def _change_sev(self, m: str, cur, prev) -> str | None:
        s = self._signed(m, cur, prev)
        if s is None:
            return None
        if self._m(m).fmt == "pct":
            return "good" if s >= 0.01 else "neutral" if s > -0.01 else "warn" if s > -0.03 else "bad"
        return "good" if s >= 0.03 else "neutral" if s > -0.03 else "warn" if s > -0.10 else "bad"

    def _change_words(self, m: str, cur, prev) -> str:
        mm = self._m(m)
        if mm.fmt == "pct":
            d = cur - prev
            return f"{'tăng' if d >= 0 else 'giảm'} {_pct_pts(d)}"
        g = (cur - prev) / abs(prev)
        return f"{'tăng' if g >= 0 else 'giảm'} {_vn(abs(g) * 100, 1)}%"

    def _best_worst(self, m: str, rows: list[dict]) -> tuple[dict, dict]:
        valid = [x for x in rows if x.get(m) is not None]
        hi = max(valid, key=lambda x: x[m])
        lo = min(valid, key=lambda x: x[m])
        return (hi, lo) if self._m(m).higher_is_better else (lo, hi)

    # ======================================================== rule groups
    def _totals(self, ctx: Ctx, m: str, obs: list, finds: list) -> None:
        r = ctx.r
        mm = self._m(m)
        tot = (r.totals or {}).get(m)
        if tot is None:
            return
        scope = "toàn bộ" if r.dims else ""
        prev = ctx.prev_tot.get(m) if ctx.prev_label else None
        line = f"{mm.label}{' ' + scope if scope else ''}: **{self._f(m, tot)}**"
        if prev is not None and self._signed(m, tot, prev) is not None:
            line += f", {self._change_words(m, tot, prev)} so với {ctx.prev_label} ({self._f(m, prev)})"
            sev = self._change_sev(m, tot, prev)
            finds.append(Finding(sev, f"{mm.label}{' ' + scope if scope else ''} {self._change_words(m, tot, prev)} "
                                      f"so với {ctx.prev_label} ({self._f(m, prev)} → {self._f(m, tot)})", m, None, "change"))
        obs.append(line + ".")
        lv = self._level(m, tot)
        if lv:
            finds.append(Finding(lv, f"{mm.label}{' ' + scope if scope else ''} đạt {self._f(m, tot)}, "
                                     f"{self._level_phrase(m, lv)}", m, None, "level"))

    def _rows(self, ctx: Ctx, m: str, obs: list, finds: list, first: bool) -> None:
        r = ctx.r
        mm = self._m(m)
        dims = r.dims
        dlabel = " × ".join(self._lc(self.cat.dimensions[d].label) for d in dims)
        rows = [x for x in r.rows if x.get(m) is not None]
        if len(rows) < 2:
            return
        hi = max(rows, key=lambda x: x[m])
        lo = min(rows, key=lambda x: x[m])
        tot = (r.totals or {}).get(m)
        share = ""
        if mm.additive and tot and tot > 0 and hi[m] >= 0:
            share = f" ({_vn(hi[m] / tot * 100, 1)}% tổng)"
        obs.append(f"{mm.label} cao nhất: **{self._row_label(hi, dims)}** {self._f(m, hi[m])}{share}; "
                   f"thấp nhất: **{self._row_label(lo, dims)}** {self._f(m, lo[m])}.")

        # Mức độ tập trung
        entity = not any(self.cat.dimensions[d].ordered for d in dims)   # nhóm tuổi nợ… không phải "đối tượng"
        if mm.additive and tot and tot > 0 and all(x[m] >= 0 for x in rows):
            if r.intent.limit:
                s = sum(x[m] for x in rows) / tot
                obs.append(f"{len(rows)} {dlabel} đứng đầu chiếm {_vn(s * 100, 1)}% tổng {self._lc(mm.label)}.")
            elif len(rows) >= 3 and hi[m] / tot >= 0.5 and first and entity:
                sev = "warn" if not mm.higher_is_better else "neutral"
                finds.append(Finding(sev, f"Mức độ tập trung cao: {self._row_label(hi, dims)} chiếm "
                                          f"{_vn(hi[m] / tot * 100, 1)}% tổng {self._lc(mm.label)}",
                                     m, self._row_label(hi, dims), "concentration"))

        # Độ phân tán của chỉ số tỷ lệ
        if mm.fmt == "pct" and hi[m] - lo[m] >= 0.03:
            obs.append(f"Chênh lệch {self._lc(mm.label)} giữa cao nhất và thấp nhất: {_pct_pts(hi[m] - lo[m])}.")

        # Ngưỡng theo từng dòng
        if mm.assess:
            for band in ("bad", "warn"):
                hit = [x for x in rows if self._level(m, x[m]) == band]
                if hit and not (len(hit) == len(rows) and self._level(m, tot) == band):
                    hit.sort(key=lambda x: x[m], reverse=not mm.higher_is_better)
                    names = ", ".join(f"{self._row_label(x, dims)} ({self._f(m, x[m])})" for x in hit[:3])
                    finds.append(Finding(band, f"{len(hit)}/{len(rows)} {dlabel} {self._level_phrase(m, band)}: {names}",
                                         m, self._row_label(hit[0], dims) if entity else None, "rows"))
                    break

        # Biến động theo từng dòng
        if not ctx.prev_label:
            return
        keyf = lambda x: tuple(x.get(d) for d in dims)  # noqa: E731
        ch = []
        for x in rows:
            p = ctx.prev_rows.get(keyf(x), {}).get(m)
            s = self._signed(m, x[m], p)
            if s is not None:
                ch.append((x, p, s))
        if not ch:
            return
        tot_prev = ctx.prev_tot.get(m)
        if mm.additive and entity and tot is not None and tot_prev is not None and tot != tot_prev:
            d_tot = tot - tot_prev
            big = max(ch, key=lambda c: (c[0][m] - c[1]) * (1 if d_tot > 0 else -1))
            d_big = big[0][m] - big[1]
            contrib = d_big / d_tot
            word = "tăng" if d_tot > 0 else "giảm"
            name = self._row_label(big[0], dims)
            if 0.3 <= contrib <= 1.0:
                obs.append(f"**{name}** đóng góp {_vn(contrib * 100, 0)}% mức {word} của toàn bộ "
                           f"({'+' if d_big >= 0 else '−'}{self._f(m, abs(d_big))}).")
            elif 1.0 < contrib <= 3.0:
                obs.append(f"**{name}** {word} {self._f(m, abs(d_big))} — lớn hơn cả mức {word} chung "
                           f"{self._f(m, abs(d_tot))} của toàn bộ (các đối tượng khác bù trừ một phần).")
        s_tot = self._signed(m, tot, tot_prev)
        worst = min(ch, key=lambda c: c[2])
        sev = self._change_sev(m, worst[0][m], worst[1])
        if sev in ("warn", "bad"):
            finds.append(Finding(sev, f"{self._row_label(worst[0], dims)}: {self._lc(mm.label)} "
                                      f"{self._change_words(m, worst[0][m], worst[1])} so với {ctx.prev_label}",
                                 m, self._row_label(worst[0], dims) if entity else None, "change"))
        if s_tot is not None and s_tot > 0:
            against = [c for c in ch if c[2] < 0]
            if against and len(against) < len(ch):
                names = ", ".join(f"{self._row_label(c[0], dims)} ({self._change_words(m, c[0][m], c[1])})"
                                  for c in sorted(against, key=lambda c: c[2])[:3])
                obs.append(f"Đi ngược xu hướng chung: {names}.")

    def _series(self, ctx: Ctx, m: str, tdim: str, obs: list, finds: list) -> None:
        mm = self._m(m)
        pts = sorted([x for x in ctx.r.rows if x.get(m) is not None], key=lambda x: x[tdim])
        if len(pts) < 2:
            return
        lbl = lambda x: self._row_label(x, [tdim])  # noqa: E731
        first, last, prev = pts[0], pts[-1], pts[-2]
        hi = max(pts, key=lambda x: x[m])
        lo = min(pts, key=lambda x: x[m])
        if self._signed(m, last[m], first[m]) is not None:
            obs.append(f"{mm.label} từ {lbl(first)} đến {lbl(last)}: {self._f(m, first[m])} → **{self._f(m, last[m])}** "
                       f"({self._change_words(m, last[m], first[m])}).")
        obs.append(f"Đỉnh: **{lbl(hi)}** ({self._f(m, hi[m])}); đáy: **{lbl(lo)}** ({self._f(m, lo[m])}).")
        sev = self._change_sev(m, last[m], prev[m])
        if sev:
            finds.append(Finding(sev, f"{mm.label} {lbl(last)} {self._change_words(m, last[m], prev[m])} so với {lbl(prev)}",
                                 m, lbl(last), "change"))
        streak = 0
        for a, b in zip(reversed(pts[:-1]), reversed(pts[1:])):
            s = self._signed(m, b[m], a[m])
            if s is not None and s < 0:
                streak += 1
            else:
                break
        if streak >= 2:
            finds.append(Finding("warn", f"{mm.label} xấu đi {streak} kỳ liên tiếp tính đến {lbl(last)}",
                                 m, lbl(last), "streak"))
        # kỳ biến động mạnh nhất
        jumps = [(b, a, self._signed(m, b[m], a[m])) for a, b in zip(pts[:-1], pts[1:])]
        jumps = [j for j in jumps if j[2] is not None]
        if jumps:
            j = min(jumps, key=lambda j: j[2])
            if self._change_sev(m, j[0][m], j[1][m]) == "bad" and j[0] is not last:
                recovered = self._change_sev(m, last[m], j[0][m]) == "good"   # kỳ cuối đã cải thiện so với điểm xấu
                finds.append(Finding("warn" if recovered else "bad", f"{lbl(j[0])} là kỳ xấu đi mạnh nhất: {self._lc(mm.label)} "
                                            f"{self._change_words(m, j[0][m], j[1][m])} so với {lbl(j[1])}",
                                     m, lbl(j[0]), "change"))
        lv = self._level(m, last[m])
        if lv:
            finds.append(Finding(lv, f"{mm.label} {lbl(last)} đạt {self._f(m, last[m])}, {self._level_phrase(m, lv)}",
                                 m, lbl(last), "level"))

    def _series_by_cat(self, ctx: Ctx, m: str, tdim: str, cdim: str, obs: list, finds: list) -> None:
        mm = self._m(m)
        groups: dict = {}
        for x in ctx.r.rows:
            if x.get(m) is not None:
                groups.setdefault(x[cdim], []).append(x)
        moves = []
        for c, pts in groups.items():
            pts.sort(key=lambda x: x[tdim])
            if len(pts) >= 2:
                s = self._signed(m, pts[-1][m], pts[-2][m])
                if s is not None:
                    moves.append((c, pts[-2], pts[-1], s))
                elif pts[-2][m] == 0 and pts[-1][m] != 0:
                    moves.append((c, pts[-2], pts[-1], (1 if pts[-1][m] > 0 else -1) * (1 if mm.higher_is_better else -1)))
        if not moves:
            return
        tl = lambda x: self._row_label(x, [tdim])  # noqa: E731
        worst = min(moves, key=lambda v: v[3])
        best = max(moves, key=lambda v: v[3])
        last_lbl = tl(worst[2])
        def words(v):
            return (self._change_words(m, v[2][m], v[1][m]) if v[1][m] not in (0, None)
                    else f"phát sinh mới {self._f(m, v[2][m])}")
        obs.append(f"Tại {last_lbl}: **{worst[0]}** có {self._lc(mm.label)} {words(worst)} so với kỳ trước "
                   f"({self._f(m, worst[1][m])} → {self._f(m, worst[2][m])}).")
        if best[0] != worst[0] and best[3] >= (0.01 if mm.fmt == "pct" else 0.03):
            obs.append(f"Tích cực nhất tại {tl(best[2])}: **{best[0]}** ({words(best)}).")
        sev = "bad" if worst[3] <= -0.10 else "warn" if worst[3] < -0.03 else "neutral"
        if mm.fmt == "pct":
            sev = "bad" if worst[3] <= -0.03 else "warn" if worst[3] < -0.01 else "neutral"
        finds.append(Finding(sev, f"{worst[0]}: {self._lc(mm.label)} {words(worst)} tại {last_lbl} so với {tl(worst[1])}",
                             m, str(worst[0]), "change"))
        last_t = max(x[tdim] for x in ctx.r.rows)
        cur = [(c, pts[-1][m]) for c, pts in groups.items() if pts[-1][tdim] == last_t]
        if mm.additive and cur:
            tot = sum(v for _, v in cur)
            c, v = max(cur, key=lambda z: z[1])
            if tot > 0:
                obs.append(f"Cơ cấu tại {self._row_label({tdim: last_t}, [tdim])}: **{c}** chiếm "
                           f"{_vn(v / tot * 100, 1)}% ({self._f(m, v)}/{self._f(m, tot)}).")

    def _divergence(self, ctx: Ctx, m1: str, m2: str, obs: list, finds: list) -> None:
        r = ctx.r
        a, b = self._m(m1), self._m(m2)
        rows = [x for x in r.rows if x.get(m1) is not None and x.get(m2) is not None]
        if r.dims and len(rows) >= 2:
            top = max(rows, key=lambda x: x[m1]) if a.higher_is_better else min(rows, key=lambda x: x[m1])
            _, worst2 = self._best_worst(m2, rows)
            if top is worst2:
                name = self._row_label(top, r.dims)
                finds.append(Finding("warn", f"{name} dẫn đầu {self._lc(a.label)} ({self._f(m1, top[m1])}) nhưng có "
                                             f"{self._lc(b.label)} kém nhất ({self._f(m2, top[m2])}) — tăng trưởng "
                                             f"có thể đang đánh đổi bằng hiệu quả", m2, name, "divergence"))
        if ctx.prev_label and r.totals:
            s1 = self._signed(m1, r.totals.get(m1), ctx.prev_tot.get(m1))
            s2 = self._signed(m2, r.totals.get(m2), ctx.prev_tot.get(m2))
            if s1 is not None and s2 is not None and s1 > 0.01 and s2 < -0.005:
                finds.append(Finding("warn", f"{a.label} cải thiện nhưng {self._lc(b.label)} "
                                             f"{self._change_words(m2, r.totals[m2], ctx.prev_tot[m2])} so với {ctx.prev_label}",
                                     m2, None, "divergence"))

    # ============================================================ sections
    def _assessment(self, finds: list[Finding]) -> list[dict]:
        if not finds:
            return [{"text": "Chưa đủ cơ sở đánh giá: kết quả chỉ có một kỳ và chỉ số chưa có ngưỡng chuẩn trong "
                             "Semantic Layer. Có thể dùng các câu hỏi Gợi ý để so sánh với kỳ trước.", "sev": "none"}]
        seen, out = set(), []
        for f in sorted(finds, key=lambda f: -SEV[f.sev]):
            if f.text in seen:
                continue
            seen.add(f.text)
            out.append({"text": f.text + ".", "sev": f.sev, "label": STATUS_LABEL[f.sev]})
            if len(out) >= MAX_FINDINGS:
                break
        return out

    def _conclusion(self, ctx: Ctx, finds: list[Finding], status: str | None) -> str:
        r = ctx.r
        subject = " và ".join(self._lc(self._m(m).label) for m in r.metrics[:2])
        if status is None:
            return (f"Số liệu {subject} ({r.period_label}) đã được tổng hợp đầy đủ; chưa có căn cứ so sánh "
                    f"hoặc ngưỡng để kết luận tốt/xấu.")
        top = max(finds, key=lambda f: SEV[f.sev])
        good = [f for f in finds if f.sev == "good"]
        msg = f"Tổng thể {subject} ({r.period_label}): **{STATUS_LABEL[status].lower()}**. "
        if status in ("bad", "warn"):
            msg += f"Điểm chính — {top.text}."
            if good:
                msg += f" Điểm tích cực — {good[0].text}."
        elif status == "good":
            msg += f"{top.text}."
        else:
            msg += "Không có biến động đáng kể so với ngưỡng và kỳ so sánh."
        return msg

    def _actions(self, ctx: Ctx, finds: list[Finding], status: str | None) -> list[str]:
        r = ctx.r
        default_scope = ", ".join(v for vals in r.intent.filters.values() for v in vals) or "toàn công ty"
        best_lbl = default_scope
        cdims = [d for d in r.dims if d not in TIME_DIMS]
        m0 = r.metrics[0]
        rows0 = [x for x in r.rows if x.get(m0) is not None]
        if cdims and rows0:
            b, _ = self._best_worst(m0, rows0)
            best_lbl = self._row_label(b, cdims)
        out: list[str] = []
        used: set = set()

        def add(t: str):
            if t and t not in out:
                out.append(t)

        def worst_of(m: str) -> str:
            rows = [x for x in r.rows if x.get(m) is not None]
            if not cdims or not rows or any(self.cat.dimensions[d].ordered for d in cdims):
                return default_scope
            if ctx.prev_label:
                w = self._last_finds_rows(ctx, m, cdims[0]) if len(cdims) == 1 else None
                if w is not None:
                    return str(w)
            return self._row_label(self._best_worst(m, rows)[1], cdims)

        for f in sorted(finds, key=lambda f: -SEV[f.sev]):
            if f.sev not in ("bad", "warn"):
                continue
            who = f.row if f.row and not f.row.startswith(("Quý", "Tháng")) else worst_of(f.metric)
            if f.kind == "divergence" and f.row:
                add(f"Đánh giá lại chất lượng tăng trưởng tại {f.row}: so sánh phần doanh số tăng thêm với phần "
                    f"biên lợi nhuận và dòng tiền bị đánh đổi.")
            elif f.kind == "concentration":
                add(f"Giảm rủi ro phụ thuộc vào {f.row}: đặt hạn mức và theo dõi riêng đối tượng này.")
            elif f.kind == "streak":
                add(f"Tìm nguyên nhân {self._lc(self._m(f.metric).label)} xấu đi liên tục và đặt mục tiêu "
                    f"phục hồi cho kỳ tới.")
            tpl = self._m(f.metric).actions.get(f.sev) or self._m(f.metric).actions.get("bad" if f.sev == "warn" else "")
            if tpl and (f.metric, who) not in used:      # mỗi chỉ số × đối tượng chỉ 1 đề xuất theo mẫu
                used.add((f.metric, who))
                add(tpl.format(worst=who, best=best_lbl, period=r.period_label))
            if len(out) >= MAX_ACTIONS:
                break
        if not out and status == "good":
            tpl = self._m(m0).actions.get("good")
            if tpl:
                add(tpl.format(worst=default_scope, best=best_lbl, period=r.period_label))
        if not out:
            add("Tiếp tục theo dõi định kỳ; chưa phát hiện điểm bất thường theo các ngưỡng đã cấu hình.")
        return out[:MAX_ACTIONS]

    # ========================================================= suggestions
    def _period_phrase(self, it: Intent) -> str:
        if it.months:
            return " ".join(f"tháng {m}" for m in it.months)
        if it.quarters:
            return " ".join(f"quý {q}" for q in it.quarters)
        return ""

    def _q(self, metric: str, filters: dict, dims: list[str], period: str, compare: bool = False) -> str:
        parts = [self._m(metric).label]
        vals = [v for vs in filters.values() for v in vs]
        if vals:
            parts.append("của " + " ".join(vals))
        if dims:
            parts.append("theo " + " theo ".join(self._lc(self.cat.dimensions[d].label) for d in dims))
        if period:
            parts.append(period)
        if compare:
            parts.append("so với quý trước" if "quý" in period or not period else "so với tháng trước")
        return " ".join(parts)

    def _valid(self, q: str, metric: str, dims: list[str]) -> bool:
        try:
            raw = self.rules.parse(q)
            it = self.comp.validate(raw)
        except (IntentError, Exception):  # noqa: BLE001
            return False
        return it.metrics == [metric] and sorted(it.dimensions) == sorted(dims)

    def _suggestions(self, ctx: Ctx, question: str) -> list[str]:
        r = ctx.r
        it = r.intent
        m0 = r.metrics[0]
        period = self._period_phrase(it)
        tdims = [d for d in r.dims if d in TIME_DIMS]
        cdims = [d for d in r.dims if d not in TIME_DIMS]
        cands: list[tuple[str, str, list[str]]] = []   # (câu hỏi, metric, dims kỳ vọng)

        worst_val = None
        if cdims and len(cdims) == 1 and not self.cat.dimensions[cdims[0]].ordered:
            rows = [x for x in r.rows if x.get(m0) is not None]
            members = {str(x.get(cdims[0])): x.get(cdims[0]) for x in rows}
            flagged = [f for f in sorted(self._finds, key=lambda f: -SEV[f.sev])
                       if f.sev in ("bad", "warn") and f.row in members]
            if flagged:
                worst_val = members[flagged[0].row]
            elif rows and it.limit:
                worst_val = rows[0].get(cdims[0])
            elif rows:
                worst_val = (self._last_finds_rows(ctx, m0, cdims[0])
                             or self._best_worst(m0, rows)[1].get(cdims[0]))
        parents = {d for d, dim in self.cat.dimensions.items() if cdims and cdims[0] in dim.drill}
        drills: list = []
        if worst_val is not None:
            for tgt in self.cat.dimensions[cdims[0]].drill:
                if tgt in r.dims or tgt in it.filters:
                    continue
                f2 = {k: v for k, v in it.filters.items() if k not in parents}
                f2[cdims[0]] = [worst_val]
                drills.append((self._q(m0, f2, [tgt], period), m0, [tgt]))
        related = []
        for rel in self._m(m0).related:
            if rel in r.metrics:
                continue
            related.append((self._q(rel, it.filters, r.dims, period), rel, list(r.dims)))
        trend = []
        if not tdims:
            sup = self.cat.supported_dims(m0)
            td = "thang" if "thang" in sup else "quy" if "quy" in sup else None
            if td:
                f2 = dict(it.filters)
                if worst_val is not None:
                    f2 = {k: v for k, v in f2.items() if k not in parents}
                    f2[cdims[0]] = [worst_val]
                trend.append((self._q(m0, f2, [td], ""), m0, [td]))
        compare = []
        if not it.compare and not tdims and len(it.quarters) + len(it.months) == 1:
            compare.append((self._q(m0, it.filters, cdims, period, compare=True), m0, cdims))

        order = drills[:1] + related[:1] + trend + compare + drills[1:2] + related[1:3]
        out, seen = [], {normalize(question)}
        for q, m, dims in order:
            n = normalize(q)
            if n in seen or not self._valid(q, m, dims):
                continue
            seen.add(n)
            out.append(q[0].upper() + q[1:])
            if len(out) >= MAX_SUGGEST:
                break
        return out

    def _last_finds_rows(self, ctx: Ctx, m: str, dim: str):
        """Ưu tiên đối tượng 'xấu nhất' theo biến động kỳ trước, nếu có."""
        if not ctx.prev_label:
            return None
        worst, ws = None, None
        for x in ctx.r.rows:
            p = ctx.prev_rows.get(tuple(x.get(d) for d in ctx.r.dims), {}).get(m)
            s = self._signed(m, x.get(m), p)
            if s is not None and (ws is None or s < ws):
                worst, ws = x.get(dim), s
        return worst if ws is not None and ws < 0 else None

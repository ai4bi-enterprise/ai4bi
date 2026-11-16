"""
Pipeline AI4BI Community:
  Câu hỏi -> [BOD?] -> Router (LLM nếu có, luật nếu không) -> Compiler (kiểm duyệt + SQL)
          -> SQLite (read-only) -> Formatter tất định -> Insight Engine (Nhận xét · Đánh giá ·
             Kết luận · Đề xuất · Gợi ý) -> khối hiển thị cho WebUI
"""
from __future__ import annotations

import logging
import time

from ..config import Settings
from .bod_demo import SCENARIOS, BodDemo
from .catalog import Catalog
from .compiler import Compiler, IntentError
from .dashboard import DashboardBuilder
from .db import Database
from .formatter import Formatter
from .insights import InsightEngine
from .llm_router import LLMError, LLMRouter
from .rule_router import RuleRouter, is_bod_question
from .text import normalize

log = logging.getLogger("ai4bi")

QUICK_PROMPTS = [
    {"group": "Hỏi đáp nhanh", "tone": "green",
     "text": "Doanh thu và biên lợi nhuận gộp theo từng chi nhánh trong Quý 3?"},
    {"group": "Tra cứu công nợ", "tone": "green",
     "text": "Top 5 khách hàng đại lý có công nợ quá hạn cao nhất hiện tại?"},
    {"group": "Góc nhìn BOD - Demo", "tone": "purple", "text": SCENARIOS["cash_paradox"]},
    {"group": "Cảnh báo Quản trị - Demo", "tone": "purple", "text": SCENARIOS["margin_erosion"]},
]
MORE_PROMPTS = [
    "Doanh thu Quý 3 so với quý trước",
    "Lợi nhuận thuần theo quý",
    "Dòng tiền thực thu theo tháng",
    "Tỷ lệ đạt KPI theo chi nhánh Quý 3",
    "Nợ quá hạn trên 90 ngày theo loại khách hàng theo quý",
    "Tỷ lệ chiết khấu Nhóm A Miền Nam theo tháng",
    "Phân tích tuổi nợ Đại lý cấp 2",
    "Top 5 sản phẩm có doanh thu cao nhất Quý 3",
]
CHITCHAT = ("xin chao", "chao ban", "hello", "hi ", "cam on", "ban la ai", "ban lam duoc gi")


class Pipeline:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = Database(settings.db_path)
        self.catalog = Catalog(self.db)
        self.compiler = Compiler(self.catalog)
        self.rules = RuleRouter(self.catalog)
        self.formatter = Formatter(self.catalog)
        self.insights = InsightEngine(self.catalog, self.compiler, self.rules)
        self.meta = self.db.meta()
        self.dashboard = DashboardBuilder(self.compiler, self.rules, self.meta)
        self.llm = None
        if settings.llm_enabled:
            self.llm = LLMRouter(settings.llm_providers, self.catalog, self.compiler.data_end.year,
                                 self.meta.get("ngay_chot_du_lieu", ""))

    def _bod(self) -> BodDemo:
        gm = self.catalog.metrics.get("bien_loi_nhuan_gop")
        return BodDemo(self.db, self.s.contact_url, gm_warn=(gm.assess.get("warn", 0.19) if gm else 0.19))

    def _route(self, question: str) -> tuple[dict, str]:
        if self.llm is not None:
            try:
                return self.llm.parse(question)
            except LLMError as e:  # mọi provider lỗi/hết quota => dự phòng bằng luật, demo không gián đoạn
                log.warning("LLM router lỗi, chuyển sang Rule Router: %s", e)
                return self.rules.parse(question), "rules (LLM không khả dụng)"
        return self.rules.parse(question), "rules"

    def answer(self, question: str, bod_mode: bool = False) -> dict:
        t0 = time.perf_counter()
        q = (question or "").strip()
        nq = normalize(q) + " "
        if not q:
            return self._out("clarify", [{"type": "text", "md": "Anh/chị hãy nhập câu hỏi."}], t0)
        if any(nq.startswith(c) for c in CHITCHAT):
            return self._chitchat(t0)

        if bod_mode or is_bod_question(q):
            res = self._bod().run(q)
            return self._out("bod_demo", res["blocks"], t0, sql=res["sql"], router="bod-demo",
                             intent={"scenario": res["scenario"]})

        raw, router = self._route(q)
        if raw.get("type") == "chitchat" and not raw.get("metrics"):
            return self._chitchat(t0, router)
        return self._answer_intent(q, raw, router, t0)

    def _chitchat(self, t0: float, router: str = "rules") -> dict:
        return self._out("chitchat", [
            {"type": "text", "md": "Chào anh/chị! Em là **AI4BI Chatbot** (Community Edition). Em trả lời "
             "số liệu Bán hàng, Lợi nhuận, Dòng tiền và Công nợ của **công ty mẫu Sao Việt** qua Semantic Layer — "
             "mọi con số đều truy vấn trực tiếp từ database, kèm Nhận xét, Đánh giá, Kết luận, Đề xuất và Gợi ý."},
            {"type": "suggestions", "items": MORE_PROMPTS[:4]}], t0, router=router)

    def _answer_intent(self, q: str, raw: dict, router: str, t0: float) -> dict:
        if raw.get("type") == "bod":
            res = self._bod().run(q)
            return self._out("bod_demo", res["blocks"], t0, sql=res["sql"], router=router,
                             intent={"scenario": res["scenario"]})
        try:
            it = self.compiler.validate(raw)
        except IntentError as e:
            if router.startswith("llm"):  # LLM chọn sai key -> thử lại bằng luật trước khi hỏi lại
                try:
                    raw = self.rules.parse(q)
                    it = self.compiler.validate(raw)
                    router = "rules (LLM intent không hợp lệ)"
                except IntentError:
                    return self._clarify(e, t0, router)
            else:
                return self._clarify(e, t0, router)
        result = self.compiler.run(it)
        blocks = self.formatter.render(result)
        sql = list(result.sql)
        if self.s.insights_enabled:
            try:
                block, extra_sql = self.insights.build(result, q)
                if block:
                    blocks.append(block)
                    sql += extra_sql
            except Exception:  # noqa: BLE001  phần phân tích lỗi không được làm hỏng câu trả lời số liệu
                log.exception("Insight engine lỗi – bỏ qua phần phân tích")
        return self._out("descriptive", blocks, t0, sql=sql, router=router, intent=it.to_dict())

    def _clarify(self, e: IntentError, t0: float, router: str) -> dict:
        md = f"{e.message}\n\nAnh/chị có thể thử một trong các câu hỏi gợi ý bên dưới."
        if e.suggestions:
            md += "\n\n" + "\n".join(f"- {s}" for s in e.suggestions[:6])
        return self._out("clarify", [{"type": "text", "md": md},
                                     {"type": "suggestions", "items": MORE_PROMPTS[:4]}], t0, router=router)

    def _out(self, mode, blocks, t0, sql=None, router="rules", intent=None) -> dict:
        return {"mode": mode, "blocks": blocks, "sql": sql or [], "router": router, "intent": intent,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}

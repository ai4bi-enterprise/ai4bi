"""Chạy bộ golden chatbot – CHỈ ĐỌC (Blueprint 12.5, app/core/golden.py, golden/chat_golden.yaml).

    python tools/golden.py                          # parser luật, dữ liệu mẫu, cả dev + holdout
    python tools/golden.py --parser llm             # bản Pro: LLM Router (tốn token – chạy tay, hỏi trước)
    python tools/golden.py --parser llm --model claude-sonnet-5-5 --split holdout
    python tools/golden.py --db neon --xlsx golden.xlsx --show

Mã thoát: 0 = đạt cổng G4 (≥ 95% đúng số, không lộ phạm vi), 1 = không đạt, 2 = không chạy được.
Không sửa dữ liệu; kết quả không chứa tên khách hàng.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Bộ golden chatbot AI4BI (chỉ đọc)")
    ap.add_argument("--db", choices=["demo", "user", "neon"], default="demo")
    ap.add_argument("--parser", choices=["rules", "llm"], default="rules")
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    ap.add_argument("--model", help="model Router thay cho cấu hình (so model – Blueprint 12.5)")
    ap.add_argument("--ids", help="chỉ chạy các câu này, cách nhau dấu phẩy")
    ap.add_argument("--threshold", type=float, default=None, help="ngưỡng đúng số (mặc định 0.95)")
    ap.add_argument("--xlsx", help="xuất scorecard ra Excel")
    ap.add_argument("--show", action="store_true", help="in cả câu đạt")
    a = ap.parse_args()

    from crosscheck import readonly, setup
    setup(a.db)
    if a.parser == "rules":
        os.environ["LLM_ENABLED"] = "off"
    from app import config
    config.LLM_NARRATIVE = False          # chỉ chấm Router + engine, không gọi LLM chuyên gia
    if a.parser == "llm":
        if not config.PRO:
            print("Bản Lite chấm bằng parser luật (--parser rules).")
            return 2
        if not config.llm_on():
            print("Chưa có khoá LLM (trang quản trị hoặc .env.local) – không chấm được LLM Router.")
            return 2
        if a.model:
            from app.chat import llm
            src = llm.keys()
            chain = src.chain
            src.chain = lambda: [{**p, "model_router": a.model} for p in chain()]
    readonly()

    from app.core import golden
    try:
        results = golden.run(a.split, ids=a.ids.split(",") if a.ids else None)
    except Exception as e:  # noqa: BLE001 – báo gọn, không in chuỗi kết nối
        print(f"Không chạy được: {type(e).__name__}: {str(e)[:200]}")
        return 2
    sc = golden.score(results)
    llm_used = sum(r.parser == "llm" for r in results)
    print(f"\nGOLDEN CHATBOT – bản {config.EDITION}, parser {a.parser}{' / ' + a.model if a.model else ''}, "
          f"kho {a.db}, split {a.split}\n")
    for r in results:
        ok = r.intent_ok and r.number_ok is not False and r.scope_ok
        if ok and not a.show:
            continue
        print(f"  {'✅' if ok else '❌'} {r.id} [{r.group}/{r.user}] {r.question}  ({r.ms} ms)")
        for e in r.errors:
            print(f"       - {e}")
    print(f"\nĐúng ý {sc['intent']:.0%} · đúng số {sc['number']:.0%} ({sc['number_cases']} câu có số) · "
          f"lộ phạm vi {sc['scope_leaks']} · đạt cả 3 {sc['all_ok']:.0%} · trung vị {sc['median_ms']} ms · p95 {sc['p95_ms']} ms")
    if sc["pro_only"]:
        print(f"Câu hỏi chỉ tiêu phân hệ Pro (bản Lite chỉ kiểm không trả số): {sc['pro_only']}")
    if a.parser == "llm":
        print(f"Câu do LLM hiểu: {llm_used}/{len(results)} (còn lại LLM lỗi → luật dự phòng)")
    ok = golden.passed(sc, a.threshold or golden.THRESHOLD)
    print(("✅ Đạt" if ok else "❌ Chưa đạt") + f" cổng G4 (≥ {a.threshold or golden.THRESHOLD:.0%} đúng số, 0 lộ phạm vi)")
    if a.xlsx:
        write_xlsx(a.xlsx, results, sc)
        print(f"Đã ghi {a.xlsx}")
    return 0 if ok else 1


def write_xlsx(path: str, results, sc) -> None:
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scorecard"
    for k in ("cases", "intent", "number", "number_cases", "scope_leaks", "all_ok", "median_ms", "p95_ms"):
        ws.append([k, sc[k]])
    d = wb.create_sheet("Cau hoi")
    d.append(["Mã", "Split", "Nhóm", "Tài khoản", "Câu hỏi", "Đúng ý", "Đúng số", "Phạm vi", "Parser", "ms", "Lỗi"])
    for c in d[1]:
        c.font = Font(bold=True)
    for r in results:
        d.append([r.id, r.split, r.group, r.user, r.question, r.intent_ok,
                  "" if r.number_ok is None else r.number_ok, r.scope_ok, r.parser, r.ms, "\n".join(r.errors)])
    wb.save(path)


if __name__ == "__main__":
    sys.exit(main())

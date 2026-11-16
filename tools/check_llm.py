"""
Kiểm tra LLM miễn phí TRƯỚC khi bật cho demo (bắt buộc chạy sau khi đổi provider/model).

  1. Gọi /models của CHÍNH provider để xác nhận model ID còn tồn tại (không đoán tên model).
  2. Chạy bộ câu hỏi chuẩn, so intent LLM với đáp án kỳ vọng -> độ chính xác + độ trễ.
  3. In khuyến nghị: provider nào đạt, provider nào nên bỏ khỏi LLM_PROVIDERS.

Chạy:  python tools/check_llm.py            (dùng cấu hình trong .env)
       python tools/check_llm.py --quick    (chỉ 4 câu – tiết kiệm quota free tier)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.engine.compiler import IntentError  # noqa: E402
from app.engine.llm_router import LLMRouter  # noqa: E402
from app.engine.pipeline import Pipeline  # noqa: E402

# Console Windows mặc định dùng cp1252 -> in tiếng Việt sẽ lỗi UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# (câu hỏi, metric đầu tiên kỳ vọng, dimensions kỳ vọng, filters kỳ vọng, quarters, months, compare, limit)
CASES = [
    ("Doanh thu và biên lợi nhuận gộp theo từng chi nhánh trong Quý 3?", "doanh_thu_thuan", ["chi_nhanh"], {}, [3], [], None, None),
    ("Top 5 khách hàng đại lý có công nợ quá hạn cao nhất hiện tại?", "no_qua_han", ["khach_hang"],
     {"loai_khach_hang": ["Đại lý cấp 1", "Đại lý cấp 2"]}, [], [], None, 5),
    ("Tháng 8 miền Nam thu được bao nhiêu tiền, so với tháng trước thế nào?", "thuc_thu", [], {"chi_nhanh": ["Miền Nam"]}, [], [8], "prev", None),
    ("Các nhóm hàng bán chạy cỡ nào trong nửa cuối năm?", "doanh_thu_thuan", ["nhom_hang"], {}, [3, 4], [], None, None),
    ("Mức chiết khấu đồ uống ở phía Nam biến thiên ra sao qua từng tháng", "ty_le_chiet_khau", ["thang"],
     {"chi_nhanh": ["Miền Nam"], "nhom_hang": ["Nhóm A - Đồ uống"]}, [], [], None, None),
    ("Lãi ròng mỗi quý", "loi_nhuan_thuan", ["quy"], {}, [], [], None, None),
    ("Chi nhánh nào hoàn thành chỉ tiêu doanh số quý 3 kém nhất", "ty_le_dat_kpi", ["chi_nhanh"], {}, [3], [], None, None),
    ("Nợ xấu trên 90 ngày của đại lý cấp 2 theo chi nhánh", "no_qua_han_90", ["chi_nhanh"],
     {"loai_khach_hang": ["Đại lý cấp 2"]}, [], [], None, None),
]


def check_models(p) -> tuple[bool, str]:
    headers = {"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}
    try:
        r = httpx.get(f"{p.base_url}/models", headers=headers, timeout=p.timeout)
        r.raise_for_status()
        ids = [str(m.get("id", "")).removeprefix("models/") for m in r.json().get("data", [])]
    except Exception as e:  # noqa: BLE001
        return False, f"không gọi được /models: {e}"
    if p.model in ids:
        return True, f"model '{p.model}' có trong {len(ids)} model khả dụng"
    near = [i for i in ids if p.model.split("/")[-1].split("-")[0] in i][:8]
    return False, f"KHÔNG thấy model '{p.model}'. Gợi ý: {', '.join(near) or ', '.join(ids[:8])}"


def score(raw: dict, case) -> tuple[bool, str]:
    q, metric, dims, flt, quarters, months, compare, limit = case
    errs = []
    if not raw.get("metrics") or raw["metrics"][0] != metric:
        errs.append(f"metric={raw.get('metrics')}")
    if sorted(raw.get("dimensions") or []) != sorted(dims):
        errs.append(f"dims={raw.get('dimensions')}")
    got_f = {k: sorted(v if isinstance(v, list) else [v]) for k, v in (raw.get("filters") or {}).items()
             if k not in ("nam", "quy", "thang") and v}
    if got_f != {k: sorted(v) for k, v in flt.items()}:
        errs.append(f"filters={raw.get('filters')}")
    if sorted(raw.get("quarters") or []) != quarters or sorted(raw.get("months") or []) != months:
        errs.append(f"kỳ=Q{raw.get('quarters')} T{raw.get('months')}")
    if (raw.get("compare") or None) != compare:
        errs.append(f"compare={raw.get('compare')}")
    if limit and raw.get("limit") != limit:
        errs.append(f"limit={raw.get('limit')}")
    return not errs, "; ".join(errs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    s = load_settings()
    if not s.llm_providers:
        print("Chưa cấu hình LLM nào. Điền GEMINI_API_KEY / GROQ_API_KEY và LLM_PROVIDERS trong .env")
        return 1
    pipe = Pipeline(s)
    cases = CASES[:4] if args.quick else CASES
    rules_ok = sum(score(pipe.rules.parse(c[0]), c)[0] for c in cases)
    print(f"Rule Router (tham chiếu): {rules_ok}/{len(cases)} câu đúng\n")
    exit_code = 0
    for p in s.llm_providers:
        print(f"=== {p.label} · {p.model} · reasoning_effort={p.reasoning_effort or '-'} · json_mode={p.json_mode}")
        ok, msg = check_models(p)
        print(f"  [/models] {'OK ' if ok else 'LỖI'} {msg}")
        if not ok:
            exit_code = 1
            continue
        router = LLMRouter([p], pipe.catalog, pipe.compiler.data_end.year, pipe.meta.get("ngay_chot_du_lieu", ""))
        right, lat = 0, []
        for c in cases:
            t0 = time.perf_counter()
            try:
                raw, _ = router.parse(c[0])
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {c[0][:60]:<60} LỖI GỌI: {str(e)[:120]}")
                time.sleep(2.5)
                continue
            lat.append((time.perf_counter() - t0) * 1000)
            good, why = score(raw, c)
            try:
                pipe.compiler.validate(raw)
                valid = ""
            except IntentError as e:
                valid = f" | Compiler từ chối: {e.message}"
            right += good
            print(f"  {'✓' if good else '✗'} {c[0][:60]:<60} {lat[-1]:6.0f} ms {why}{valid}")
            time.sleep(2.5)  # tôn trọng RPM của free tier
        avg = sum(lat) / len(lat) if lat else 0
        verdict = "ĐẠT" if right >= len(cases) - 1 and avg < 8000 else "CHƯA ĐẠT"
        print(f"  => {right}/{len(cases)} đúng · trễ TB {avg:,.0f} ms · {verdict}\n")
        if verdict != "ĐẠT":
            exit_code = 1
    print("Gợi ý: đặt provider ĐẠT nhanh nhất lên đầu LLM_PROVIDERS; Rule Router luôn là lớp dự phòng cuối.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

"""
Semantic Catalog: nạp định nghĩa chỉ số/chiều từ YAML + giá trị hợp lệ từ database.
Đây là "nguồn chân lý" duy nhất mà Router (luật hoặc LLM) và bộ Phân tích được phép tham chiếu.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .db import Database
from .text import normalize, pattern_for

MODELS_DIR = Path(__file__).resolve().parents[1] / "semantic_models"
_REF = re.compile(r"\{([a-z_0-9]+)\}")
STATUS_LEVELS = ("bad", "warn", "good")


@dataclass
class Dimension:
    key: str
    label: str
    kind: str = "category"
    synonyms: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    value_aliases: dict[str, list[str]] = field(default_factory=dict)
    value_groups: dict[str, list[str]] = field(default_factory=dict)
    ordered: bool = False
    drill: list[str] = field(default_factory=list)


@dataclass
class Source:
    key: str
    table: str
    kind: str
    dimensions: dict[str, str]
    where: str = ""
    macros: dict[str, str] = field(default_factory=dict)


@dataclass
class Metric:
    key: str
    label: str
    fmt: str
    model: str
    source: str | None = None
    sql: str | None = None
    formula: str | None = None
    additive: bool = False
    higher_is_better: bool = True
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    assess: dict[str, float] = field(default_factory=dict)
    actions: dict[str, str] = field(default_factory=dict)
    related: list[str] = field(default_factory=list)

    @property
    def is_derived(self) -> bool:
        return self.formula is not None

    def refs(self) -> list[str]:
        return _REF.findall(self.formula or "")


class Catalog:
    def __init__(self, db: Database, models_dir: Path = MODELS_DIR):
        self.db = db
        self.dimensions: dict[str, Dimension] = {}
        self.sources: dict[str, Source] = {}
        self.metrics: dict[str, Metric] = {}
        self._load(models_dir)
        self._validate()

    # ------------------------------------------------------------------ load
    def _load(self, models_dir: Path) -> None:
        dim_doc = yaml.safe_load((models_dir / "dimensions.yaml").read_text(encoding="utf-8"))
        for key, d in dim_doc["dimensions"].items():
            values = list(d.get("values", []))
            if d.get("values_sql"):
                _, rows = self.db.query(d["values_sql"])
                values = [r[0] for r in rows]
            self.dimensions[key] = Dimension(
                key=key, label=d["label"], kind=d.get("kind", "category"),
                synonyms=d.get("synonyms", []), values=values,
                value_aliases=d.get("value_aliases", {}) or {}, value_groups=d.get("value_groups", {}) or {},
                ordered=bool(d.get("ordered", False)), drill=list(d.get("drill", []) or []))
        order = {"sales": 0, "finance": 1, "receivables": 2}
        for f in sorted(models_dir.glob("*.yaml"), key=lambda p: (order.get(p.stem, 9), p.stem)):
            if f.name == "dimensions.yaml":
                continue
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            for sk, s in (doc.get("sources") or {}).items():
                self.sources[sk] = Source(key=sk, table=s["table"], kind=s.get("kind", "period"),
                                          dimensions=s["dimensions"], where=s.get("where", ""),
                                          macros=s.get("macros", {}) or {})
            for mk, m in (doc.get("metrics") or {}).items():
                self.metrics[mk] = Metric(key=mk, label=m["label"], fmt=m.get("format", "number"), model=f.stem,
                                          source=m.get("source"), sql=m.get("sql"), formula=m.get("formula"),
                                          additive=bool(m.get("additive", False)),
                                          higher_is_better=bool(m.get("higher_is_better", True)),
                                          description=m.get("description", ""), synonyms=m.get("synonyms", []),
                                          assess=m.get("assess", {}) or {}, actions=m.get("actions", {}) or {},
                                          related=list(m.get("related", []) or []))

    def _validate(self) -> None:
        for m in self.metrics.values():
            if m.is_derived:
                for r in m.refs():
                    if r not in self.metrics:
                        raise ValueError(f"Chỉ số {m.key} tham chiếu chỉ số không tồn tại: {r}")
                ast.parse(_REF.sub(lambda x: x.group(1), m.formula), mode="eval")
            elif m.source not in self.sources:
                raise ValueError(f"Chỉ số {m.key} dùng nguồn không tồn tại: {m.source}")
            for r in m.related:
                if r not in self.metrics:
                    raise ValueError(f"Chỉ số {m.key}: related tham chiếu chỉ số không tồn tại: {r}")
            for lv in m.actions:
                if lv not in STATUS_LEVELS:
                    raise ValueError(f"Chỉ số {m.key}: actions chỉ nhận {STATUS_LEVELS}, gặp '{lv}'")
            if m.assess:
                if set(m.assess) != {"good", "warn"}:
                    raise ValueError(f"Chỉ số {m.key}: assess phải có đúng 2 khóa good, warn")
                ok = m.assess["good"] >= m.assess["warn"] if m.higher_is_better else m.assess["good"] <= m.assess["warn"]
                if not ok:
                    raise ValueError(f"Chỉ số {m.key}: ngưỡng good/warn ngược chiều higher_is_better")
        for s in self.sources.values():
            for d in s.dimensions:
                if d not in self.dimensions:
                    raise ValueError(f"Nguồn {s.key} khai báo chiều không tồn tại: {d}")
        for d in self.dimensions.values():
            for x in d.drill:
                if x not in self.dimensions:
                    raise ValueError(f"Chiều {d.key}: drill tham chiếu chiều không tồn tại: {x}")

    # --------------------------------------------------------------- helpers
    def base_metrics(self, key: str) -> list[str]:
        """Danh sách chỉ số gốc (có SQL) cần để tính một chỉ số (đệ quy cho chỉ số dẫn xuất)."""
        m = self.metrics[key]
        if not m.is_derived:
            return [key]
        out: list[str] = []
        for r in m.refs():
            for b in self.base_metrics(r):
                if b not in out:
                    out.append(b)
        return out

    def is_snapshot(self, key: str) -> bool:
        return all(self.sources[self.metrics[b].source].kind == "snapshot" for b in self.base_metrics(key))

    def supported_dims(self, metric_key: str) -> set[str]:
        dims: set[str] | None = None
        for b in self.base_metrics(metric_key):
            src = self.sources[self.metrics[b].source]
            d = set(src.dimensions)
            if src.kind == "snapshot":
                d |= {"nam", "quy", "thang"}  # xử lý bằng nhiều ngày chốt
            dims = d if dims is None else dims & d
        return dims or set()

    def match_value(self, dim: str, value: str) -> str | None:
        """Ánh xạ giá trị người dùng/LLM đưa ra về giá trị chuẩn có thật trong DB."""
        d = self.dimensions[dim]
        nv = normalize(str(value))
        for v in d.values:
            if normalize(v) == nv:
                return v
        for canon, aliases in d.value_aliases.items():
            if nv in (normalize(a) for a in aliases):
                return canon
        return None

    def value_patterns(self) -> list[tuple[str, list[str], re.Pattern, int]]:
        """(dim, [giá trị chuẩn], pattern, độ dài) – dùng cho bộ định tuyến theo luật."""
        out = []
        for dk, d in self.dimensions.items():
            if d.kind == "time":
                continue
            for v in d.values:
                n = normalize(v)
                if len(n) >= 4:
                    out.append((dk, [v], pattern_for(v), len(n)))
            for canon, aliases in d.value_aliases.items():
                for a in aliases:
                    out.append((dk, [canon], pattern_for(a), len(normalize(a))))
            for g, vals in d.value_groups.items():
                out.append((dk, list(vals), pattern_for(g), len(normalize(g))))
        out.sort(key=lambda x: -x[3])
        return out

    def describe_for_llm(self) -> str:
        lines = ["CHỈ SỐ (metrics):"]
        for m in self.metrics.values():
            dims = ", ".join(sorted(self.supported_dims(m.key)))
            lines.append(f"- {m.key}: {m.label} [{m.fmt}] – chiều hỗ trợ: {dims}")
        lines.append("CHIỀU (dimensions):")
        for d in self.dimensions.values():
            vals = ""
            if d.kind != "time" and d.values and len(d.values) <= 12:
                vals = " – giá trị: " + " | ".join(d.values)
            lines.append(f"- {d.key}: {d.label}{vals}")
        return "\n".join(lines)

    def public(self) -> dict:
        return {
            "metrics": [{"key": m.key, "label": m.label, "format": m.fmt, "model": m.model,
                         "derived": m.is_derived, "description": m.description,
                         "higher_is_better": m.higher_is_better, "assess": m.assess,
                         "dimensions": sorted(self.supported_dims(m.key))} for m in self.metrics.values()],
            "dimensions": [{"key": d.key, "label": d.label, "kind": d.kind,
                            "values": d.values if len(d.values) <= 12 else d.values[:12] + ["…"]}
                           for d in self.dimensions.values()],
        }

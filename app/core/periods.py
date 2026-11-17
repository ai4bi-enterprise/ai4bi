"""Kỳ báo cáo theo tháng. Mọi tính toán theo KỲ ĐANG CHỌN, không theo ngày hệ thống (Blueprint bài học 13)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass

MODES = {"month": "Tháng", "quarter": "Quý", "ytd": "Lũy kế năm"}


@dataclass(frozen=True)
class Period:
    year: int
    m_from: int
    m_to: int
    mode: str = "month"
    y_from: int | None = None  # kỳ vắt năm (VD 12 tháng gần nhất)

    @property
    def start(self) -> int:
        return (self.y_from or self.year) * 100 + self.m_from

    @property
    def end(self) -> int:
        return self.year * 100 + self.m_to

    @property
    def months(self) -> int:
        return (self.year - (self.y_from or self.year)) * 12 + self.m_to - self.m_from + 1

    @property
    def days(self) -> int:
        total, y, m = 0, self.y_from or self.year, self.m_from
        for _ in range(self.months):
            total += calendar.monthrange(y, m)[1]
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return total

    @property
    def label(self) -> str:
        if self.y_from and self.y_from != self.year:
            return f"T{self.m_from}/{self.y_from}–T{self.m_to}/{self.year}"
        if self.mode == "quarter":
            return f"Q{(self.m_to - 1) // 3 + 1}/{self.year}"
        if self.mode == "ytd":
            return f"Năm {self.year}" if self.m_to == 12 else f"Lũy kế T1–T{self.m_to}/{self.year}"
        if self.months > 1:
            return f"T{self.m_from}–T{self.m_to}/{self.year}"
        return f"T{self.m_to}/{self.year}"

    def yoy(self) -> "Period":
        return Period(self.year - 1, self.m_from, self.m_to, self.mode,
                      self.y_from - 1 if self.y_from else None)

    def shift(self, months: int) -> "Period":
        def mv(y: int, m: int) -> tuple[int, int]:
            i = y * 12 + m - 1 + months
            return i // 12, i % 12 + 1
        fy, fm = mv(self.y_from or self.year, self.m_from)
        ty, tm = mv(self.year, self.m_to)
        return Period(ty, fm, tm, self.mode, fy if fy != ty else None)

    def prev(self) -> "Period":
        """Kỳ liền trước cùng độ dài: tháng trước / cùng số tháng của quý trước / cùng kỳ năm trước (lũy kế)."""
        return self.shift({"month": -1, "quarter": -3}.get(self.mode, -12))

    def boy(self) -> "Period":
        """Số dư đầu năm = cuối tháng 12 năm trước (dùng cho nguồn snapshot)."""
        return Period(self.year - 1, 12, 12, "month")

    def end_month(self) -> "Period":
        return Period(self.year, self.m_to, self.m_to, "month")

    def trailing(self, n: int) -> "Period":
        i = self.year * 12 + self.m_to - 1 - (n - 1)
        y, m = i // 12, i % 12 + 1
        return Period(self.year, m, self.m_to, "month", y if y != self.year else None)

    def to_dict(self) -> dict:
        return {"year": self.year, "month": self.m_to, "mode": self.mode, "label": self.label,
                "start": self.start, "end": self.end}


def make(year: int, month: int, mode: str = "month") -> Period:
    if mode == "quarter":
        return Period(year, (month - 1) // 3 * 3 + 1, month, "quarter")
    if mode == "ytd":
        return Period(year, 1, month, "ytd")
    return Period(year, month, month, "month")


def ym_label(ym: int) -> str:
    return f"T{ym % 100}/{str(ym // 100)[2:]}"

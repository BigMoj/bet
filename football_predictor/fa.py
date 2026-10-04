"""Persian (Farsi) labels and number formatting."""

from __future__ import annotations

_DIGITS = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")

REASON = {"injury": "مصدوم", "suspension": "محروم", "doubtful": "مشکوک", "unavailable": "غایب"}
POSITION = {"GK": "دروازه‌بان", "DEF": "مدافع", "MID": "هافبک", "FWD": "مهاجم", None: ""}
CONFIDENCE = {"high": "بالا", "medium": "متوسط", "low": "پایین"}
FORM_ICON = {"W": "🟢", "D": "🟡", "L": "🔴"}


def num(value) -> str:
    return str(value).translate(_DIGITS)


def dec(value: float, places: int = 1) -> str:
    return num(f"{value:.{places}f}")


def pct(p: float) -> str:
    return num(f"{round(p * 100)}") + "٪"


def form_icons(results: str) -> str:
    return "".join(FORM_ICON.get(r, "") for r in results)

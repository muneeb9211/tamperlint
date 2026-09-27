"""Text helpers shared by content, geometry and logic detectors."""

from __future__ import annotations

import re
from collections.abc import Iterable
from statistics import median

from tamperlint.document import Char, Page, Word, strip_subset_prefix

_STYLE_WORDS = (
    "ExtraBold|UltraBold|SemiBold|DemiBold|Semilight|ExtraLight|UltraLight|BoldItalic"
    "|BoldOblique|LightItalic|Bold|Italic|Oblique|Roman|Regular|Medium|Light|Demi|Black"
    "|Heavy|Book|Thin|Condensed|Cond|Narrow|Bd|It|BI"
)
_STYLE_SUFFIX = re.compile(rf"[-,](?:{_STYLE_WORDS})+$|MT$|PSMT$|PS$", re.IGNORECASE)
_BOLD = re.compile(r"bold|black|heavy|demi|\bbd\b|-bd|^(?:cm|ec|sf|lm)b", re.IGNORECASE)
# TeX font names: family prefix, a short shape code and a design size (CMBX10, SFRM1000)
_TEX_FONT = re.compile(r"^(cm|ec|sf|lm)[a-z]{1,5}\d{1,4}$", re.IGNORECASE)
SYMBOL_FONTS = ("symbol", "zapf", "dingbat", "wingding", "webding")

# Amounts must carry exactly two decimals, so years, counts and codes are never read as money.
_CURRENCY = re.compile(r"^(?:rs\.?|pkr|inr|usd|eur|gbp|aed|sar|\$|€|£|₨|₹)\s?", re.IGNORECASE)
_MARKER = re.compile(r"\s?(cr|dr)\.?$", re.IGNORECASE)
_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+\.\d{2}$")  # 1,234.56
_LAKH = re.compile(r"^\d{1,2}(?:,\d{2})+,\d{3}\.\d{2}$")  # 1,00,000.00 (South Asian grouping)
_PLAIN = re.compile(r"^\d+\.\d{2}$")  # 1234.56
_EUROPEAN = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{2}$|^\d+,\d{2}$")  # 1.234,56 / 1234,56


def font_family(fontname: str) -> str:
    """ "ABCDEF+ArialMT" -> "Arial"; "BitstreamVeraSans-Bold" -> "BitstreamVeraSans"."""
    base = strip_subset_prefix(fontname)
    tex = _TEX_FONT.match(base)
    if tex:  # CMR10, CMBX10 and CMTI10 are cuts of one Computer Modern family
        return tex.group(1).lower()
    previous = None
    while previous != base:
        previous = base
        base = _STYLE_SUFFIX.sub("", base)
    return base.lower() or strip_subset_prefix(fontname).lower()


def is_bold(fontname: str) -> bool:
    return bool(_BOLD.search(strip_subset_prefix(fontname)))


def is_symbol_font(fontname: str) -> bool:
    lower = fontname.lower()
    return any(s in lower for s in SYMBOL_FONTS)


def parse_amount(text: str) -> tuple[float, str | None] | None:
    """Parse a monetary amount. Returns ``(value, marker)`` where ``marker`` is ``"DR"``,
    ``"CR"`` or ``None``; negative forms (``-5.00``, ``(5.00)``, ``5.00-``) give a negative value.
    """
    t = text.strip()
    marker = None
    if m := _MARKER.search(t):
        if not t[: m.start()].strip()[-1:].isdigit():
            return None
        marker = m.group(1).upper()
        t = t[: m.start()].strip()
    negative = False
    if t.startswith("(") or t.endswith(")"):
        if not (t.startswith("(") and t.endswith(")")):
            return None  # unbalanced parenthesis
        negative, t = True, t[1:-1].strip()
    if t.startswith(("-", "+")):
        negative, t = t[0] == "-", t[1:].strip()
    elif t.endswith("-"):
        negative, t = True, t[:-1].strip()
    t = _CURRENCY.sub("", t)
    if _GROUPED.match(t) or _LAKH.match(t) or _PLAIN.match(t):
        value = float(t.replace(",", ""))
    elif _EUROPEAN.match(t):
        value = float(t.replace(".", "").replace(",", "."))
    else:
        return None
    return (-value if negative else value), marker


def parse_money(text: str) -> float | None:
    """Signed amount; a ``Dr`` marker makes the value negative (as for an overdrawn balance)."""
    parsed = parse_amount(text)
    if parsed is None:
        return None
    value, marker = parsed
    return -abs(value) if marker == "DR" else value


def visible_chars(page: Page) -> list[Char]:
    return [c for c in page.chars if not c.hidden and c.text.strip()]


class CharIndex:
    """Characters bucketed by vertical position, so finding a word's characters does not scan
    the whole page (pages of dense text hold tens of thousands of characters)."""

    STEP = 12.0

    def __init__(self, chars: Iterable[Char]) -> None:
        self.buckets: dict[int, list[Char]] = {}
        for c in chars:
            self.buckets.setdefault(int(c.top // self.STEP), []).append(c)

    def near(self, top: float, bottom: float) -> list[Char]:
        first, last = int((top - 0.5) // self.STEP), int((bottom + 0.5) // self.STEP)
        return [c for key in range(first, last + 1) for c in self.buckets.get(key, ())]


def word_chars(
    page: Page, word: Word, chars: Iterable[Char] | CharIndex | None = None
) -> list[Char]:
    if isinstance(chars, CharIndex):
        pool: Iterable[Char] = chars.near(word.top, word.bottom)
    else:
        pool = chars if chars is not None else visible_chars(page)
    return [
        c
        for c in pool
        if c.x0 >= word.x0 - 0.5
        and c.x1 <= word.x1 + 0.5
        and c.top >= word.top - 0.5
        and c.bottom <= word.bottom + 0.5
    ]


def group_rows(words: list[Word]) -> list[list[Word]]:
    """Group words into visual rows by vertical overlap (robust to small baseline shifts)."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.top + w.bottom) / 2):
        centre = (w.top + w.bottom) / 2
        if rows:
            last = rows[-1]
            ref = median((x.top + x.bottom) / 2 for x in last)
            height = median(x.bottom - x.top for x in last)
            if abs(centre - ref) <= 0.45 * max(height, 1.0):
                last.append(w)
                continue
        rows.append([w])
    return [sorted(r, key=lambda w: w.x0) for r in rows]

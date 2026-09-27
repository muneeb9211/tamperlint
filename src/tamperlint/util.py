"""Small helpers shared across modules."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta, timezone

PARSER_LOGGERS = ("pdfminer", "pyhanko", "pyhanko_certvalidator", "pikepdf")


def quiet_parser_logs() -> None:
    """Silence the warnings third-party parsers log about the input, often with tracebacks.

    They describe recoverable oddities of the document, and tamperlint reports what matters in
    its own findings. Applications (CLI, server, demo) call this; the library never does, so
    callers keep control of their logging.
    """
    for name in PARSER_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)


_PDF_DATE_RE = re.compile(
    r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)


def parse_pdf_date(value: str | None) -> datetime | None:
    """Parse a PDF date string (``D:YYYYMMDDHHmmSSOHH'mm'``) or an ISO 8601 XMP date."""
    if not value:
        return None
    text = value.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", text):  # XMP date-only values
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3] or 1), tzinfo=UTC)
        except ValueError:
            return None
    if "T" in text or (len(text) >= 10 and text[4] == "-"):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    m = _PDF_DATE_RE.match(text)
    if not m:
        return None
    year, month, day, hour, minute, second, zulu, sign, tzh, tzm = m.groups()
    try:
        tz = UTC
        if sign:
            offset = timedelta(hours=int(tzh or 0), minutes=int(tzm or 0))
            tz = timezone(offset if sign == "+" else -offset)
        elif zulu:
            tz = UTC
        return datetime(
            int(year),
            int(month or 1),
            int(day or 1),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=tz,
        )
    except ValueError:
        return None


def overlap_ratio(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """Fraction of rectangle ``a`` (x0, top, x1, bottom) covered by rectangle ``b``."""
    ax0, at, ax1, ab = a
    bx0, bt, bx1, bb = b
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ab, bb) - max(at, bt)
    if w <= 0 or h <= 0:
        return 0.0
    area = (ax1 - ax0) * (ab - at)
    return (w * h) / area if area > 0 else 0.0

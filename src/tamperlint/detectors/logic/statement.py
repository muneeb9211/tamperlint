"""TL-LOGIC-001 / TL-LOGIC-003: bank-statement arithmetic and dates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from tamperlint.detectors.base import Detector
from tamperlint.document import Document, Word
from tamperlint.logic.columns import TableColumns, header_end, layouts
from tamperlint.models import Finding, Layer
from tamperlint.text import group_rows, parse_amount

TOLERANCE = 0.011
_DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y")
_DATE_RE = re.compile(
    r"\b(\d{1,2}[ ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[ ]\d{4}"
    r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)


_HEADER_WORDS = {
    "debit": "debit", "debits": "debit", "withdrawal": "debit", "withdrawals": "debit",
    "credit": "credit", "credits": "credit", "deposit": "credit", "deposits": "credit",
    "amount": "amount", "balance": "balance",
}  # fmt: skip
_TWO_WORD_HEADERS = {
    ("paid", "out"): "debit", ("money", "out"): "debit",
    ("paid", "in"): "credit", ("money", "in"): "credit",
}  # fmt: skip
_OPENING = ("opening balance", "balance brought forward", "brought forward", "balance b/f")
_CLOSING = ("closing balance", "balance carried forward", "carried forward", "balance c/f")
_MARKERS = {"dr", "dr.", "cr", "cr."}


def _norm(text: str) -> str:
    return text.lower().strip(":.()")


def _header_columns(row: list[Word]) -> dict[str, tuple[float, float]]:
    """Column spans (x0, x1) of a transaction-table header row, or {} if this is not one."""
    names_at: dict[str, tuple[int, int]] = {}  # column -> (first word, last word) of its name
    words = [_norm(w.text) for w in row]
    for i in range(len(row)):
        pair = (words[i], words[i + 1]) if i + 1 < len(row) else None
        if pair in _TWO_WORD_HEADERS:
            names_at.setdefault(_TWO_WORD_HEADERS[pair], (i, i + 1))
        elif words[i] in _HEADER_WORDS:
            names_at.setdefault(_HEADER_WORDS[words[i]], (i, i))
    has_flows = ("debit" in names_at and "credit" in names_at) or "amount" in names_at
    if "balance" not in names_at or not has_flows:
        return {}
    stops = {first for first, _ in names_at.values()}
    return {
        name: (row[first].x0, header_end(row, last, stops))
        for name, (first, last) in names_at.items()
    }


def _amounts(row: list[Word]) -> list[tuple[Word, float, str | None]]:
    """Amounts in reading order, attaching a separate "Dr"/"Cr" word to the amount before it."""
    out: list[tuple[Word, float, str | None]] = []
    for i, w in enumerate(row):
        parsed = parse_amount(w.text)
        if parsed is None:
            continue
        value, marker = parsed
        if marker is None and i + 1 < len(row) and _norm(row[i + 1].text) in {"dr", "cr"}:
            nxt = row[i + 1]
            if nxt.x0 - w.x1 < 2.5 * max(w.size, 1.0):
                marker = _norm(nxt.text).upper()
        out.append((w, value, marker))
    return out


def _rightmost_amount(row: list[Word]) -> Word | None:
    amounts = _amounts(row)
    return amounts[-1][0] if amounts else None


def _labelled_amount(
    row: list[Word], amounts: list[tuple[Word, float, str | None]], labels: tuple[str, ...]
) -> tuple[Word, float] | None:
    """The first amount to the right of a label such as "Opening balance" in this row."""
    text = " ".join(_norm(w.text) for w in row)
    for label in labels:
        if label not in text:
            continue
        # x position where the label ends: the last word of the label's first occurrence
        tokens = label.split()
        for i in range(len(row) - len(tokens) + 1):
            if [_norm(w.text) for w in row[i : i + len(tokens)]] == tokens:
                end = row[i + len(tokens) - 1].x1
                after = [a for a in amounts if a[0].x0 >= end]
                if after:
                    w, value, marker = after[0]
                    return w, (-abs(value) if marker == "DR" else value)
                return None
    return None


def parse_date(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class TxRow:
    page: int
    words: list[Word]
    debit: float
    credit: float
    balance: float
    balance_word: Word
    when: date | None


class StatementDetector(Detector):
    name = "statement-logic"
    layer = Layer.LOGIC
    rule_ids = ("TL-LOGIC-001", "TL-LOGIC-003")

    def applies(self, doc: Document) -> str | None:
        if doc.doc_type != "statement":
            return f"document type is {doc.doc_type}, not a bank statement"
        return None if any(p.words for p in doc.pages) else "no text layer"

    def run(self, doc: Document) -> list[Finding]:
        opening, rows, closing = self._parse(doc)
        if len(rows) < 2:
            doc.load_warnings.append("Statement rows could not be parsed; balance checks skipped.")
            return []
        return self._reconcile(opening, rows, closing) + self._dates(doc, rows)

    # ------------------------------------------------------------------ parsing
    def _parse(
        self, doc: Document
    ) -> tuple[float | None, list[TxRow], tuple[float, Word, int] | None]:
        opening: float | None = None
        closing: tuple[float, Word, int] | None = None
        rows: list[TxRow] = []
        numbered = [(page.number, row) for page in doc.pages for row in group_rows(page.words)]
        tables = layouts([row for _, row in numbered], _header_columns, _rightmost_amount)
        # Column positions persist across pages: many statements print the header once.
        columns = TableColumns({})
        for index, (page_number, row) in enumerate(numbered):
            if index in tables:
                columns = tables[index]
                continue
            amounts = _amounts(row)
            label_opening = _labelled_amount(row, amounts, _OPENING)
            if label_opening is not None:
                opening = label_opening[1]
                continue
            label_closing = _labelled_amount(row, amounts, _CLOSING)
            if label_closing is not None:
                closing = (label_closing[1], label_closing[0], page_number)
                continue
            if not columns or not amounts:
                continue
            assigned: dict[str, tuple[Word, float, str | None]] = {}
            for w, value, marker in amounts:
                column = columns.column_of(w)
                if column is not None:
                    assigned[column] = (w, value, marker)
            if "balance" not in assigned:
                continue
            debit = credit = 0.0
            if "debit" in assigned:
                debit = abs(assigned["debit"][1])
            if "credit" in assigned:
                credit = abs(assigned["credit"][1])
            if "amount" in assigned:
                _, value, marker = assigned["amount"]
                if marker == "DR" or (marker is None and value < 0):
                    debit = abs(value)
                else:
                    credit = abs(value)
            bal_word, bal_value, bal_marker = assigned["balance"]
            if bal_marker == "DR":
                bal_value = -abs(bal_value)
            m = _DATE_RE.search(" ".join(w.text for w in row))
            rows.append(
                TxRow(
                    page=page_number,
                    words=row,
                    debit=debit,
                    credit=credit,
                    balance=bal_value,
                    balance_word=bal_word,
                    when=parse_date(m.group(1)) if m else None,
                )
            )
        return opening, rows, closing

    # ------------------------------------------------------------------ checks
    def _reconcile(
        self, opening: float | None, rows: list[TxRow], closing: tuple[float, Word, int] | None
    ) -> list[Finding]:
        findings: list[Finding] = []
        prev = opening
        for i, row in enumerate(rows):
            if prev is not None:
                expected = round(prev - row.debit + row.credit, 2)
                if abs(expected - row.balance) > TOLERANCE:
                    findings.append(
                        self.finding(
                            "TL-LOGIC-001",
                            f"Row {i + 1} on page {row.page}: previous balance {prev:,.2f} "
                            f"- debit {row.debit:,.2f} + credit {row.credit:,.2f} "
                            f"= {expected:,.2f}, "
                            f"but the statement shows {row.balance:,.2f} "
                            f"(difference {row.balance - expected:+,.2f}).",
                            confidence=0.9,
                            page=row.page,
                            bbox=row.balance_word.bbox,
                            evidence={
                                "row": i + 1,
                                "previous": prev,
                                "debit": row.debit,
                                "credit": row.credit,
                                "stated": row.balance,
                                "expected": expected,
                            },
                        )
                    )
            prev = row.balance
        if closing and abs(closing[0] - rows[-1].balance) > TOLERANCE:
            findings.append(
                self.finding(
                    "TL-LOGIC-001",
                    f"The closing balance {closing[0]:,.2f} does not match the last running "
                    f"balance {rows[-1].balance:,.2f}.",
                    confidence=0.85,
                    page=closing[2],
                    bbox=closing[1].bbox,
                    evidence={"closing": closing[0], "last_balance": rows[-1].balance},
                )
            )
        return findings

    def _dates(self, doc: Document, rows: list[TxRow]) -> list[Finding]:
        period = re.search(
            r"(?:period|from)[:\s]+(.+?)\s+(?:to|-|–)\s+(.+?)(?:\s{2,}|$|\n)",  # noqa: RUF001 (en dash)
            doc.text,
            re.IGNORECASE,
        )
        if not period:
            return []
        m1, m2 = _DATE_RE.search(period.group(1)), _DATE_RE.search(period.group(2))
        start = parse_date(m1.group(1)) if m1 else None
        end = parse_date(m2.group(1)) if m2 else None
        if not (start and end) or end < start:
            return []
        slack = timedelta(days=1)
        return [
            self.finding(
                "TL-LOGIC-003",
                f"A transaction on page {row.page} is dated {row.when:%d %b %Y}, outside the "
                f"statement period {start:%d %b %Y} to {end:%d %b %Y}.",
                confidence=0.7,
                page=row.page,
                bbox=row.words[0].bbox,
                evidence={
                    "date": row.when.isoformat(),
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
            )
            for row in rows
            if row.when and not (start - slack <= row.when <= end + slack)
        ]

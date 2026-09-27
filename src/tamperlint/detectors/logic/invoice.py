"""TL-LOGIC-002: invoice line items, subtotal, charges, tax and total.

Invoices vary a lot, so every check only runs when its inputs are unambiguous:

* line arithmetic (qty x unit price = amount) needs a header row naming the Qty and Amount
  columns, so serial numbers and item codes are never read as quantities;
* the totals block is read by label; several tax and charge lines are summed, lines such as
  "Includes VAT" are informational, and a total that matches either a tax-exclusive or a
  tax-inclusive reading is accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tamperlint.detectors.base import Detector
from tamperlint.document import Document, Word
from tamperlint.logic.columns import TableColumns, header_end, layouts
from tamperlint.models import Finding, Layer
from tamperlint.text import group_rows, parse_money

TOLERANCE = 0.02
_NUMBER = re.compile(r"^\d{1,6}(?:\.\d{1,3})?$")

_COLUMN_WORDS = {
    "qty": {"qty", "quantity", "units", "hrs", "hours"},
    "unit": {"price", "rate", "unit price", "unit cost", "cost"},
    "discount": {"discount", "disc"},
    "amount": {"amount", "line total", "net amount", "total"},
}
_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "Total (incl. GST)" is the total of a tax-inclusive invoice, not an informational line
    ("total", re.compile(r"^\W*(grand |invoice )?total\b.*\b(incl\.?|including|inclusive)")),
    ("info", re.compile(r"\b(incl\.?|includes|including|inclusive)\b")),
    ("subtotal", re.compile(r"sub-?\s?total|net total|total before tax|total excl")),
    ("paid", re.compile(r"\b(amount paid|paid|payment received|deposit)\b")),
    ("due", re.compile(r"\b(balance due|amount due|total due|due)\b")),
    ("discount", re.compile(r"\b(discount|rebate)\b")),
    ("tax", re.compile(r"\b(tax|vat|gst|cgst|sgst|igst|hst|pst|sales tax)\b")),
    ("charge", re.compile(r"\b(shipping|delivery|freight|handling|service charge|surcharge"
                          r"|postage|insurance|fee)\b")),
    ("total", re.compile(r"\b(grand total|invoice total|total amount|total)\b")),
)  # fmt: skip


@dataclass
class _Totals:
    subtotal: tuple[float, Word, int] | None = None
    total: tuple[float, Word, int] | None = None
    due: tuple[float, Word, int] | None = None
    paid: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    charges: float = 0.0
    seen: set[str] = field(default_factory=set)


def _columns(row: list[Word]) -> dict[str, tuple[float, float]]:
    """Column spans from an item-table header row (needs Qty and Amount), else {}."""
    names_at: dict[str, tuple[int, int]] = {}  # column -> (first word, last word) of its name
    words = [w.text.lower().strip(":.") for w in row]
    i = 0
    while i < len(row):
        pair = f"{words[i]} {words[i + 1]}" if i + 1 < len(row) else ""
        for name, names in _COLUMN_WORDS.items():
            if pair in names:
                names_at.setdefault(name, (i, i + 1))
                i += 1
                break
            if words[i] in names:
                names_at.setdefault(name, (i, i))
                break
        i += 1
    if "qty" not in names_at or "amount" not in names_at:
        return {}
    stops = {first for first, _ in names_at.values()}
    return {
        name: (row[first].x0, header_end(row, last, stops))
        for name, (first, last) in names_at.items()
    }


def _rightmost_money(row: list[Word]) -> Word | None:
    return next((w for w in reversed(row) if parse_money(w.text) is not None), None)


def _has_quantity(row: list[Word], columns: TableColumns) -> bool:
    return any(columns.column_of(w) == "qty" and _NUMBER.match(w.text) for w in row)


class InvoiceDetector(Detector):
    name = "invoice-logic"
    layer = Layer.LOGIC
    rule_ids = ("TL-LOGIC-002",)

    def applies(self, doc: Document) -> str | None:
        if doc.doc_type != "invoice":
            return f"document type is {doc.doc_type}, not an invoice"
        return None if any(p.words for p in doc.pages) else "no text layer"

    def run(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        items: list[float] = []
        totals = _Totals()
        numbered = [(page.number, row) for page in doc.pages for row in group_rows(page.words)]
        tables = layouts([row for _, row in numbered], _columns, _rightmost_money)
        columns = TableColumns({})
        in_table = False
        for index, (page_number, row) in enumerate(numbered):
            if index in tables:
                columns, in_table = tables[index], True
                continue
            money = [(w, v) for w in row if (v := parse_money(w.text)) is not None]
            label = " ".join(w.text for w in row if parse_money(w.text) is None).lower()
            kind = next((k for k, pattern in _LABELS if pattern.search(label)), None)
            # Inside the item table a row with a quantity is an item, even when its
            # description contains a word like "tax" ("Tax return preparation").
            if in_table and columns and (kind is None or _has_quantity(row, columns)):
                item = self._item(row, columns, page_number, findings)
                if item is not None:
                    items.append(item)
                    continue
            if kind is not None and money:
                in_table = False
                self._record(totals, kind, money[-1][1], money[-1][0], page_number)
        findings += self._check_totals(items, totals)
        return findings

    def _item(
        self, row: list[Word], columns: TableColumns, page: int, findings: list[Finding]
    ) -> float | None:
        cells: dict[str, Word] = {}
        for w in row:
            column = columns.column_of(w)
            if column and column not in cells:
                cells[column] = w
        amount_word = cells.get("amount")
        amount = parse_money(amount_word.text) if amount_word else None
        if amount is None or amount_word is None:
            return None
        qty_word, unit_word = cells.get("qty"), cells.get("unit")
        if qty_word and unit_word and _NUMBER.match(qty_word.text):
            qty = float(qty_word.text)
            unit = parse_money(unit_word.text)
            discount = parse_money(cells["discount"].text) if "discount" in cells else 0.0
            if unit is not None and discount is not None:
                expected = round(qty * unit - abs(discount), 2)
                if abs(expected - amount) > TOLERANCE:
                    detail = f" - discount {abs(discount):,.2f}" if discount else ""
                    findings.append(
                        self.finding(
                            "TL-LOGIC-002",
                            f"On page {page}, {qty:g} x {unit:,.2f}{detail} = {expected:,.2f}, "
                            f"but the line amount is {amount:,.2f}.",
                            confidence=0.85,
                            page=page,
                            bbox=amount_word.bbox,
                            evidence={
                                "qty": qty,
                                "unit": unit,
                                "discount": discount,
                                "amount": amount,
                            },
                        )
                    )
        return amount

    @staticmethod
    def _record(totals: _Totals, kind: str, value: float, word: Word, page: int) -> None:
        totals.seen.add(kind)
        if kind == "subtotal":
            totals.subtotal = (value, word, page)
        elif kind == "total" and totals.total is None:
            totals.total = (value, word, page)
        elif kind == "due":
            totals.due = (value, word, page)
        elif kind == "paid":
            totals.paid += abs(value)
        elif kind == "discount":
            totals.discount += abs(value)
        elif kind == "tax":
            totals.tax += value
        elif kind == "charge":
            totals.charges += value

    def _check_totals(self, items: list[float], t: _Totals) -> list[Finding]:
        findings: list[Finding] = []
        if t.subtotal and items:
            value, word, page = t.subtotal
            summed = round(sum(items), 2)
            if abs(summed - value) > TOLERANCE:
                findings.append(
                    self.finding(
                        "TL-LOGIC-002",
                        f"The line items add up to {summed:,.2f}, but the subtotal shows "
                        f"{value:,.2f}.",
                        confidence=0.85,
                        page=page,
                        bbox=word.bbox,
                        evidence={"sum_of_lines": summed, "subtotal": value},
                    )
                )
        total = t.total or (t.due if not t.paid else None)
        base = t.subtotal[0] if t.subtotal else (round(sum(items), 2) if items else None)
        if total and base is not None:
            value, word, page = total
            exclusive = round(base - t.discount + t.charges + t.tax, 2)
            inclusive = round(base - t.discount + t.charges, 2)  # prices already include tax
            if abs(exclusive - value) > TOLERANCE and abs(inclusive - value) > TOLERANCE:
                findings.append(
                    self.finding(
                        "TL-LOGIC-002",
                        f"Subtotal {base:,.2f}, discounts {t.discount:,.2f}, charges "
                        f"{t.charges:,.2f} and tax {t.tax:,.2f} give {exclusive:,.2f}, but the "
                        f"total shows {value:,.2f} (difference {value - exclusive:+,.2f}).",
                        confidence=0.9,
                        page=page,
                        bbox=word.bbox,
                        evidence={"expected": exclusive, "stated": value},
                    )
                )
        if t.total and t.due and t.paid:
            expected_due = round(t.total[0] - t.paid, 2)
            value, word, page = t.due
            if abs(expected_due - value) > TOLERANCE:
                findings.append(
                    self.finding(
                        "TL-LOGIC-002",
                        f"Total {t.total[0]:,.2f} minus payments {t.paid:,.2f} is "
                        f"{expected_due:,.2f}, but the balance due shows {value:,.2f}.",
                        confidence=0.75,
                        page=page,
                        bbox=word.bbox,
                        evidence={"expected": expected_due, "stated": value},
                    )
                )
        return findings

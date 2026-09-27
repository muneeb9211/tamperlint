"""Generate genuine-looking synthetic documents with ground truth.

Every document is issued by a fictional institution, uses fake account numbers and carries a
visible "SPECIMEN · SYNTHETIC" watermark. The generators exist to test tamperlint; they must
never be used to imitate a real organisation.
"""

from __future__ import annotations

import io
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

try:
    import reportlab
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Synthetic generation needs the 'synth' extra: pip install tamperlint[synth]"
    ) from exc

FICTIONAL_BANKS = ("Demo Bank Ltd", "Northwind Savings Bank", "Ravi River Bank", "Contoso Finance")
FIRST_NAMES = (
    "Ayesha",
    "Bilal",
    "Fatima",
    "Hamza",
    "Zainab",
    "Omar",
    "Sana",
    "Usman",
    "Maria",
    "Daniel",
)
LAST_NAMES = (
    "Qureshi",
    "Siddiqui",
    "Malik",
    "Chaudhry",
    "Rahman",
    "Iqbal",
    "Hussain",
    "Baig",
    "Carter",
    "Silva",
)
MERCHANTS = (
    "Grocery Mart",
    "Fuel Station 24",
    "Electric Utility",
    "Mobile Top-up",
    "Online Store",
    "Pharmacy Plus",
    "Cafe Latte",
    "School Fee Payment",
    "Internet Services",
    "Book House",
)
PAYERS = ("Client Payment", "Cash Deposit", "Transfer from Savings", "Profit Payout")
WATERMARK = "SPECIMEN · SYNTHETIC"
FONT, FONT_BOLD = "Vera", "VeraBd"
PAGE_W, PAGE_H = A4
MIN_BALANCE = 1_000.0


def register_fonts() -> None:
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    font_dir = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    pdfmetrics.registerFont(TTFont(FONT, os.path.join(font_dir, "Vera.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(font_dir, "VeraBd.ttf")))


def money(value: float) -> str:
    return f"{value:,.2f}"


def fake_iban(rng: random.Random) -> str:
    """A Pakistan-format IBAN with a valid mod-97 checksum and a fictional bank code."""
    bban = "DEMO" + "".join(str(rng.randint(0, 9)) for _ in range(16))
    rearranged = bban + "PK00"
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    check = 98 - int(digits) % 97
    return f"PK{check:02d}{bban}"


@dataclass
class Cell:
    """Where a value was drawn, in PDF points with origin at the bottom-left."""

    text: str
    x_right: float
    y: float
    size: float
    font: str = FONT


@dataclass
class Row:
    day: str
    description: str
    debit: float
    credit: float
    balance: float
    cells: dict[str, Cell] = field(default_factory=dict)


@dataclass
class StatementTruth:
    bank: str
    holder: str
    iban: str
    period_start: str
    period_end: str
    opening_balance: float
    closing_balance: float
    rows: list[Row]
    closing_cell: Cell | None = None
    page_height: float = PAGE_H

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _watermark(c: Canvas) -> None:
    c.saveState()
    c.setFillColorRGB(0.85, 0.85, 0.85)
    c.setFont(FONT_BOLD, 54)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, WATERMARK)
    c.restoreState()


def make_statement(
    seed: int = 0,
    *,
    rows: int = 16,
    overrides: dict[int, dict[str, float]] | None = None,
    creator: str = "eStatements Generator 4.2",
) -> tuple[bytes, StatementTruth]:
    """Build a one-page bank statement. ``overrides`` lets a caller change row amounts before
    balances are computed (used to create a "rebuilt from scratch" forgery)."""
    register_fonts()
    rng = random.Random(seed)
    bank = rng.choice(FICTIONAL_BANKS)
    holder = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    start = date(2026, rng.randint(1, 8), 1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    opening = round(rng.uniform(20_000, 250_000), 2)

    days = sorted(rng.sample(range((end - start).days + 1), min(rows, (end - start).days + 1)))
    balance = opening
    items: list[Row] = []
    for i, offset in enumerate(days[:rows]):
        is_credit = rng.random() < 0.3
        amount = round(rng.uniform(500, 60_000) if is_credit else rng.uniform(150, 18_000), 2)
        topped_up = not is_credit and balance - amount < MIN_BALANCE
        if topped_up:
            # A genuine savings account is not overdrawn: money comes in before it runs out.
            is_credit, amount = True, round(rng.uniform(20_000, 60_000), 2)
        debit, credit = (0.0, amount) if is_credit else (amount, 0.0)
        if overrides and i in overrides:
            debit = overrides[i].get("debit", debit)
            credit = overrides[i].get("credit", credit)
        balance = round(balance - debit + credit, 2)
        desc = "Salary / Transfer In" if is_credit and rng.random() < 0.5 else rng.choice(MERCHANTS)
        if topped_up:
            desc = "Salary / Transfer In"
        elif is_credit and desc in MERCHANTS:
            # Money comes in from payers, not shops. Mapping the drawn merchant (instead of
            # drawing again) keeps the random sequence, and so every later row, unchanged.
            desc = PAYERS[MERCHANTS.index(desc) % len(PAYERS)]
        items.append(
            Row((start + timedelta(days=offset)).strftime("%d %b %Y"), desc, debit, credit, balance)
        )

    truth = StatementTruth(
        bank=bank,
        holder=holder,
        iban=fake_iban(rng),
        period_start=start.strftime("%d %b %Y"),
        period_end=end.strftime("%d %b %Y"),
        opening_balance=opening,
        closing_balance=balance,
        rows=items,
    )

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"{bank} account statement")
    c.setCreator(creator)
    c.setAuthor(bank)
    _watermark(c)

    c.setFont(FONT_BOLD, 16)
    c.drawString(40, 800, bank)
    c.setFont(FONT, 10)
    c.drawString(40, 784, "Account Statement")
    c.drawRightString(555, 800, f"Statement period: {truth.period_start} to {truth.period_end}")
    c.drawString(40, 752, f"Account holder: {holder}")
    c.drawString(40, 738, f"IBAN: {truth.iban}")
    c.drawString(40, 724, f"Opening balance: {money(opening)}")

    cols = {"debit": 420.0, "credit": 490.0, "balance": 555.0}
    y = 690.0
    c.setFont(FONT_BOLD, 9)
    c.drawString(40, y, "Date")
    c.drawString(110, y, "Description")
    for name, x in cols.items():
        c.drawRightString(x, y, name.capitalize())
    c.line(40, y - 5, 555, y - 5)
    c.setFont(FONT, 9)
    for row in items:
        y -= 18
        c.drawString(40, y, row.day)
        c.drawString(110, y, row.description)
        for name, x in cols.items():
            value = getattr(row, name)
            text = money(value) if (value or name == "balance") else ""
            if text:
                c.drawRightString(x, y, text)
                row.cells[name] = Cell(text=text, x_right=x, y=y, size=9)
    y -= 26
    c.setFont(FONT_BOLD, 10)
    c.drawString(40, y, "Closing balance")
    c.drawRightString(555, y, money(balance))
    truth.closing_cell = Cell(text=money(balance), x_right=555, y=y, size=10, font=FONT_BOLD)
    c.setFont(FONT, 7)
    c.drawString(
        40, 40, f"{WATERMARK}: generated by tamperlint for testing. Not a real bank document."
    )
    c.showPage()
    c.save()
    return buf.getvalue(), truth


@dataclass
class InvoiceTruth:
    seller: str
    items: list[dict[str, Any]]
    subtotal: float
    tax_rate: float
    tax: float
    total: float
    cells: dict[str, Cell] = field(default_factory=dict)


def make_invoice(
    seed: int = 0, *, items: int = 6, total_override: float | None = None
) -> tuple[bytes, InvoiceTruth]:
    register_fonts()
    rng = random.Random(seed + 10_000)
    seller = rng.choice(("Demo Supplies Co.", "Northwind Traders", "Contoso Office Mart"))
    lines: list[dict[str, Any]] = []
    for _ in range(items):
        qty = rng.randint(1, 12)
        unit = round(rng.uniform(150, 9_000), 2)
        lines.append(
            {
                "item": rng.choice(
                    ("Printer paper", "Toner", "Desk lamp", "Cable set", "Notebook", "Chair mat")
                ),
                "qty": qty,
                "unit": unit,
                "amount": round(qty * unit, 2),
            }
        )
    subtotal = round(sum(line["amount"] for line in lines), 2)
    tax_rate = 0.17
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2) if total_override is None else total_override
    truth = InvoiceTruth(seller, lines, subtotal, tax_rate, tax, total)

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"Invoice from {seller}")
    c.setCreator("Invoicing Suite 7")
    _watermark(c)
    c.setFont(FONT_BOLD, 16)
    c.drawString(40, 800, seller)
    c.setFont(FONT, 10)
    c.drawString(40, 784, f"Invoice No. INV-{rng.randint(10000, 99999)}")
    c.drawString(40, 770, "Bill to: Demo Customer")
    y = 730.0
    c.setFont(FONT_BOLD, 9)
    for text, x in (("Item", 40), ("Qty", 330), ("Unit price", 430), ("Amount", 555)):
        (c.drawString if x == 40 else c.drawRightString)(x, y, text)
    c.setFont(FONT, 9)
    for line in lines:
        y -= 18
        c.drawString(40, y, line["item"])
        c.drawRightString(330, y, str(line["qty"]))
        c.drawRightString(430, y, money(line["unit"]))
        c.drawRightString(555, y, money(line["amount"]))
    for label, value, key in (
        ("Subtotal", subtotal, "subtotal"),
        ("Tax (17%)", tax, "tax"),
        ("Total", total, "total"),
    ):
        y -= 20
        font = FONT_BOLD if key == "total" else FONT
        c.setFont(font, 10)
        c.drawRightString(430, y, label)
        c.drawRightString(555, y, money(value))
        truth.cells[key] = Cell(money(value), 555, y, 10, font)
    c.setFont(FONT, 7)
    c.drawString(40, 40, f"{WATERMARK}: generated by tamperlint for testing. Not a real invoice.")
    c.showPage()
    c.save()
    return buf.getvalue(), truth

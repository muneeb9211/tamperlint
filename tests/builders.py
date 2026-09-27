"""Small document builders for regression tests (genuine layouts from other generators)."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

W, H = A4


def pdf(draw: Callable[[Canvas], None]) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1)
    draw(c)
    c.save()
    return buf.getvalue()


def statement(
    rows: Iterable[tuple[str, str, str, str, str]],
    *,
    header_align: str = "right",
    summary: str | None = None,
    title: str = "Northwind Savings Bank - Account Statement",
) -> bytes:
    """A one-page statement: rows of (date, description, debit, credit, balance)."""

    def draw(c: Canvas) -> None:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, H - 50, title)
        c.setFont("Helvetica", 9)
        c.drawString(50, H - 70, "Account number 0012345678")
        y = H - 100
        if summary:
            c.drawString(50, y, summary)
            y -= 30
        c.setFont("Helvetica-Bold", 9)
        c.drawString(50, y, "Date")
        c.drawString(120, y, "Description")
        if header_align == "left":
            c.drawString(330, y, "Debit")
            c.drawString(410, y, "Credit")
            c.drawString(490, y, "Balance")
        else:
            c.drawRightString(380, y, "Debit")
            c.drawRightString(460, y, "Credit")
            c.drawRightString(540, y, "Balance")
        y -= 16
        c.setFont("Helvetica", 9)
        for date, desc, debit, credit, balance in rows:
            c.drawString(50, y, date)
            c.drawString(120, y, desc)
            if debit:
                c.drawRightString(380, y, debit)
            if credit:
                c.drawRightString(460, y, credit)
            c.drawRightString(540, y, balance)
            y -= 14

    return pdf(draw)


def multipage_statement(transactions: int = 70) -> bytes:
    """Header printed once on page 1; rows continue on later pages."""

    def draw(c: Canvas) -> None:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, H - 50, "Northwind Savings Bank - Account Statement")
        c.setFont("Helvetica", 9)
        c.drawString(50, H - 70, "Statement period: 01 Jan 2024 to 31 Jan 2024")
        y = H - 110
        c.setFont("Helvetica-Bold", 9)
        c.drawString(50, y, "Date")
        c.drawString(120, y, "Description")
        c.drawRightString(380, y, "Debit")
        c.drawRightString(460, y, "Credit")
        c.drawRightString(540, y, "Balance")
        y -= 16
        c.setFont("Helvetica", 9)
        c.drawString(120, y, "Opening balance")
        c.drawRightString(540, y, "10,000.00")
        y -= 14
        balance, page = 10_000.0, 1
        for i in range(transactions):
            if y < 60:
                c.drawString(50, 30, f"Page {page}")
                c.showPage()
                page += 1
                y = H - 60
                c.setFont("Helvetica", 9)
            amount = round(10 + (i * 37) % 490 + 0.25, 2)
            is_debit = i % 3 != 0
            balance = round(balance - amount if is_debit else balance + amount, 2)
            c.drawString(50, y, f"{i % 28 + 1:02d} Jan 2024")
            c.drawString(120, y, "Card purchase" if is_debit else "Transfer in")
            c.drawRightString(380 if is_debit else 460, y, f"{amount:,.2f}")
            c.drawRightString(540, y, f"{balance:,.2f}")
            y -= 14
        c.drawString(120, y, "Closing balance")
        c.drawRightString(540, y, f"{balance:,.2f}")
        c.drawString(50, 30, f"Page {page}")

    return pdf(draw)


def invoice(
    header: list[str],
    items: list[list[str]],
    totals: list[tuple[str, str]],
    xs: list[tuple[float, str]],
) -> bytes:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, H - 50, "INVOICE")
        c.setFont("Helvetica", 9)
        c.drawString(50, H - 70, "Bill To: Contoso Finance    Invoice No. 1042")
        y = H - 110
        c.setFont("Helvetica-Bold", 9)
        for (x, align), text in zip(xs, header, strict=True):
            (c.drawRightString if align == "r" else c.drawString)(x, y, text)
        y -= 16
        c.setFont("Helvetica", 9)
        for item in items:
            for (x, align), text in zip(xs, item, strict=True):
                (c.drawRightString if align == "r" else c.drawString)(x, y, text)
            y -= 14
        y -= 10
        for label, value in totals:
            c.drawString(380, y, label)
            c.drawRightString(540, y, value)
            y -= 14

    return pdf(draw)

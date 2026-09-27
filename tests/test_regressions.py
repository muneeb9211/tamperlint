"""Regression tests for defects found in the pre-release review.

Each test reproduces a reported problem: genuine layouts that were flagged (false positives),
edits that were missed (false negatives), and crashes on unusual input.
"""

from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Callable

import numpy as np
import pdfplumber
import pikepdf
import pytest
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

from tamperlint import Verdict, check
from tamperlint.detectors.content.fonts import FontDetector
from tamperlint.detectors.logic.identifiers import iban_candidate, iban_is_valid
from tamperlint.document import Char, Document, Page
from tamperlint.loaders import UnsupportedFileError, load_bytes, sniff_kind
from tamperlint.rules import RULES, Rule, register_rule
from tamperlint.synth import benign, forge, raster
from tamperlint.synth.genuine import MERCHANTS, MIN_BALANCE, make_statement
from tamperlint.synth.samples import build_samples
from tamperlint.text import parse_amount, parse_money
from tamperlint.util import parse_pdf_date

from .builders import invoice, multipage_statement, pdf, statement

ROWS = [
    ("02 Jan 2024", "Rent", "200.00", "", "800.00"),
    ("03 Jan 2024", "Salary", "", "50.00", "850.00"),
    ("04 Jan 2024", "Fuel", "100.00", "", "750.00"),
    ("05 Jan 2024", "Groceries", "25.00", "", "725.00"),
]
OPENING = ("01 Jan 2024", "Opening balance", "", "", "1,000.00")


def rules_of(data: bytes, name: str = "doc.pdf") -> set[str]:
    return {f.rule_id for f in check(data, name=name).findings if f.severity.value != "info"}


# --------------------------------------------------------------------------- statements


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(multipage_statement(), id="header-on-page-1-only"),
        pytest.param(statement([OPENING, *ROWS], header_align="left"), id="left-aligned-header"),
        pytest.param(
            statement(
                ROWS,
                summary="Opening Balance: 1,000.00     Total Debits: 325.00     "
                "Closing Balance: 725.00",
            ),
            id="summary-line",
        ),
        pytest.param(
            statement(
                [
                    ("01 Jan 2024", "Opening balance", "", "", "100.00 Cr"),
                    ("02 Jan 2024", "Rent", "200.00", "", "100.00 Dr"),
                    ("03 Jan 2024", "Salary", "", "500.00", "400.00 Cr"),
                ]
            ),
            id="dr-cr-markers",
        ),
        pytest.param(
            statement(
                [
                    ("01 Jan 2024", "Opening balance", "", "", "100.00"),
                    ("02 Jan 2024", "Rent", "200.00", "", "(100.00)"),
                    ("03 Jan 2024", "Salary", "", "500.00", "400.00"),
                ]
            ),
            id="parenthesised-negatives",
        ),
        pytest.param(
            statement(
                [
                    ("01 Jan 2024", "Opening balance", "", "", "Rs.1,000.00"),
                    ("02 Jan 2024", "Rent", "Rs.200.00", "", "Rs.800.00"),
                    ("03 Jan 2024", "Salary", "", "Rs.50.00", "Rs.850.00"),
                ]
            ),
            id="rupee-prefix",
        ),
    ],
)
def test_genuine_statement_layouts_are_intact(data: bytes) -> None:
    report = check(data, name="statement.pdf")
    assert report.doc_type == "statement"
    assert report.verdict is Verdict.INTACT, [(f.rule_id, f.message) for f in report.findings]


def test_statement_arithmetic_still_caught_in_other_layouts() -> None:
    broken = [OPENING, ROWS[0], ("03 Jan 2024", "Salary", "", "50.00", "950.00"), *ROWS[2:]]
    assert "TL-LOGIC-001" in rules_of(statement(broken, header_align="left"))


# --------------------------------------------------------------------------- invoices

X4 = [(50.0, "l"), (330.0, "r"), (430.0, "r"), (540.0, "r")]
X5 = [(50.0, "l"), (80.0, "l"), (330.0, "r"), (430.0, "r"), (540.0, "r")]
HEADER4 = ["Description", "Qty", "Unit Price", "Amount"]
ITEMS = [["Widget", "3", "10.00", "30.00"], ["Gadget", "2", "25.00", "50.00"]]


@pytest.mark.parametrize(
    ("header", "items", "totals", "xs"),
    [
        pytest.param(
            ["#", "Description", "Qty", "Unit Price", "Amount"],
            [["1", "Widget", "3", "10.00", "30.00"], ["2", "Gadget", "1", "25.00", "25.00"]],
            [("Subtotal", "55.00"), ("Tax", "5.50"), ("Total", "60.50")],
            X5,
            id="serial-column",
        ),
        pytest.param(
            ["Code", "Description", "Qty", "Unit Price", "Amount"],
            [["4471", "Widget", "3", "10.00", "30.00"], ["5120", "Gadget", "2", "25.00", "50.00"]],
            [("Subtotal", "80.00"), ("Tax", "8.00"), ("Total", "88.00")],
            X5,
            id="item-code-column",
        ),
        pytest.param(
            HEADER4, ITEMS,
            [("Subtotal", "80.00"), ("Shipping", "15.00"), ("Tax", "8.00"), ("Total", "103.00")],
            X4, id="shipping",
        ),
        pytest.param(
            HEADER4, ITEMS,
            [("Subtotal", "80.00"), ("CGST 9%", "7.20"), ("SGST 9%", "7.20"), ("Total", "94.40")],
            X4, id="two-taxes",
        ),
        pytest.param(
            HEADER4, [["Widget", "3", "40.00", "120.00"]],
            [("Total", "120.00"), ("Includes VAT 20%", "20.00")],
            X4, id="vat-inclusive",
        ),
        pytest.param(
            HEADER4,
            [["Tax return preparation", "1", "300.00", "300.00"],
             ["Bookkeeping", "2", "100.00", "200.00"]],
            [("Subtotal", "500.00"), ("Sales tax", "50.00"), ("Total", "550.00")],
            X4, id="item-named-tax",
        ),
        pytest.param(
            HEADER4, ITEMS,
            [("Subtotal", "80.00"), ("Tax", "8.00"), ("Grand Total", "88.00"),
             ("Amount Paid", "50.00"), ("Balance Due", "38.00")],
            X4, id="grand-total-and-balance-due",
        ),
        pytest.param(
            ["Description", "Qty", "Unit Price", "Discount", "Amount"],
            [["Widget", "3", "10.00", "3.00", "27.00"], ["Gadget", "2", "25.00", "0.00", "50.00"]],
            [("Subtotal", "77.00"), ("Tax", "7.70"), ("Total", "84.70")],
            [(50.0, "l"), (250.0, "r"), (330.0, "r"), (430.0, "r"), (540.0, "r")],
            id="line-discount",
        ),
    ],
)  # fmt: skip
def test_genuine_invoice_layouts_are_intact(
    header: list[str],
    items: list[list[str]],
    totals: list[tuple[str, str]],
    xs: list[tuple[float, str]],
) -> None:
    report = check(invoice(header, items, totals, xs), name="invoice.pdf")
    assert report.doc_type == "invoice"
    assert report.verdict is Verdict.INTACT, [(f.rule_id, f.message) for f in report.findings]


def test_inflated_invoice_total_is_caught() -> None:
    data = invoice(
        HEADER4, ITEMS, [("Subtotal", "$80.00"), ("Tax", "$8.00"), ("Total", "$90.00")], X4
    )
    assert "TL-LOGIC-002" in rules_of(data)


# --------------------------------------------------------------------------- parsing helpers


@pytest.mark.parametrize(
    ("text", "value"),
    [("Rs.1,000.00", 1000.0), ("$1,000.00", 1000.0), ("€1.000,00", 1000.0),
     ("1,00,000.00", 100000.0), ("1,000.00 Dr", -1000.0), ("1,000.00CR", 1000.0),
     ("(1,000.00", None), ("1,000.00)", None), ("-(5.00)", None)],
)  # fmt: skip
def test_money_formats(text: str, value: float | None) -> None:
    assert parse_money(text) == value


def test_amount_markers() -> None:
    assert parse_amount("250.00 Cr") == (250.0, "CR")
    assert parse_amount("250.00") == (250.0, None)


def test_xmp_date_only_values() -> None:
    parsed = parse_pdf_date("2023-01")
    assert parsed is not None and (parsed.year, parsed.month, parsed.day) == (2023, 1, 1)
    assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def test_iban_lengths_and_formats() -> None:
    valid = "PK36SCBL0000001123456702"
    assert iban_is_valid(valid)
    edits = [valid[:i] + d + valid[i + 1 :] for i in range(4, len(valid)) for d in "0123456789"]
    assert not any(iban_is_valid(e) for e in edits if e != valid)
    assert iban_candidate("pk36-scbl-0000-0011-2345-6702 Opening balance") == valid
    assert iban_candidate("GB82 WEST 1234 5698 7654 32 BIC WESTGB2L") == "GB82WEST12345698765432"


def test_png_containing_pdf_marker_is_an_image() -> None:
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40 + b"%PDF-1.7" + b"\x00" * 100
    assert sniff_kind(data, "scan.png") == "image"


# --------------------------------------------------------------------------- fonts and overlays


def test_generator_subsets_with_different_glyphs_are_not_flagged() -> None:
    page = Page(number=1, width=595, height=842)
    page.chars = [Char(t, "QWXTZK+Arial", 9, i, i + 5, 100, 109) for i, t in enumerate("abcdef")]
    page.chars += [Char(t, "MBNVCX+Arial", 9, i, i + 5, 120, 129) for i, t in enumerate("αβγδ")]
    doc = Document(name="x.pdf", data=b"", sha256="", kind="pdf", pages=[page])
    assert FontDetector()._subsets(doc) == []
    page.chars.append(Char("a", "MBNVCX+Arial", 9, 50, 55, 140, 149))  # the same glyph twice
    found = FontDetector()._subsets(doc)
    assert [f.rule_id for f in found] == ["TL-FONT-001"]
    assert found[0].severity.value == "low"  # letters, not a retyped value


def test_subset_added_to_retype_an_amount() -> None:
    """Acrobat-style editing embeds the font again for the edited characters: a small subset
    of digits beside the original one. Office and LibreOffice number their subsets from a
    counter and may repeat glyphs across them in one export; they are left alone."""
    page = Page(number=1, width=595, height=842)
    body = "Closing balance 12,480.00 Opening balance 9,200.00"
    page.chars = [
        Char(t, "QWXTZK+Arial", 9, i * 5, i * 5 + 5, 100, 109) for i, t in enumerate(body)
    ]
    page.chars += [
        Char(t, "MBNVCX+Arial", 9, 400 + i * 5, 405 + i * 5, 100, 109)
        for i, t in enumerate("1,250.00")
    ]
    doc = Document(name="x.pdf", data=b"", sha256="", kind="pdf", pages=[page])
    found = FontDetector()._subsets(doc)
    assert [(f.rule_id, f.severity.value) for f in found] == [("TL-FONT-001", "medium")]
    for ch in page.chars:
        ch.fontname = ch.fontname.replace("QWXTZK", "BCDEEE").replace("MBNVCX", "BCDIEE")
    assert FontDetector()._subsets(doc) == []


def test_whiteout_drawn_as_a_path_hides_text() -> None:
    """PyMuPDF closes the rectangle path of a correction box once more ("re h ... f"), which
    pdfminer then reports as a curve rather than a rectangle."""

    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(50, 760, "Harbour City Bank - Account Statement")
        c.drawString(50, 700, "Closing balance")
        c.drawRightString(300, 700, "1,250.00")
        c._code.append("q 255.21 697.45 46.04 12.17 re h 1 1 1 rg f Q")  # as PyMuPDF writes it
        c.setFillColorRGB(0, 0, 0)
        c.drawRightString(300, 700, "9,999.00")

    data = pdf(draw)
    with pdfplumber.open(io.BytesIO(data)) as doc:
        assert doc.pages[0].curves and not doc.pages[0].rects  # the premise of this test
    found = [f for f in check(data).findings if f.rule_id == "TL-OVL-001"]
    assert found and found[0].severity.value == "medium", [f.message for f in found]
    assert found[0].evidence["hidden_text"] == "1,250.00"


def test_panel_over_text_is_layering_not_a_patch() -> None:
    """Slides put panels over text boxes and new labels on the panel; a correction patch
    hugs the text it hides."""

    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(60, 700, "Advanced paper")
        c.setFillColorRGB(1, 1, 1)
        c.rect(40, 560, 420, 200, stroke=0, fill=1)  # a panel, far larger than the text
        c.setFillColorRGB(0, 0, 0)
        c.drawString(60, 700, "High specification")

    found = [f for f in check(pdf(draw)).findings if f.rule_id == "TL-OVL-001"]
    assert found and all(f.severity.value == "low" for f in found)


def _value_rows(offsets: list[float]) -> bytes:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(50, 780, "Harbour City Bank - Account Statement")
        for i, shift in enumerate(offsets):
            y = 740 - 18 * i
            c.drawString(50, y, f"0{i % 9 + 1} Mar 2026")
            c.drawString(150, y, "Grocery Mart")
            c.drawRightString(400, y - shift, f"{1_000 + 37 * i:,.2f}")
            c.drawRightString(500, y, f"{9_000 - 37 * i:,.2f}")

    return pdf(draw)


def test_uneven_tables_are_not_read_as_retyped_values() -> None:
    level = [0.0] * 8
    assert "TL-GEO-001" in rules_of(_value_rows([0.0, 0.0, 1.0, *level]))  # one value off
    uneven = [0.7, -0.9, 1.4, -1.8, 0.8, 1.6, -1.1, 0.0, 0.0]  # varied offsets: layout
    assert "TL-GEO-001" not in rules_of(_value_rows(uneven))
    assert "TL-GEO-001" not in rules_of(_value_rows([0.0, 0.0, 4.0, *level]))  # half a line


def _with_info(data: bytes, **info: str) -> bytes:
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(data)) as doc:
        for key, value in info.items():
            doc.docinfo[f"/{key}"] = value
        doc.save(out)
    return out.getvalue()


def _severity(data: bytes, rule: str) -> set[str]:
    return {f.severity.value for f in check(data).findings if f.rule_id == rule}


def test_online_tools_matter_for_financial_documents_only() -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        c.drawString(50, 760, "Community garden newsletter")

    statement_pdf = make_statement(1)[0]
    assert _severity(_with_info(statement_pdf, Producer="iLovePDF"), "TL-META-001") == {"medium"}
    assert _severity(_with_info(pdf(draw), Producer="iLovePDF"), "TL-META-001") == {"low"}


def test_time_zone_labels_do_not_reverse_dates() -> None:
    base = make_statement(1)[0]
    mislabelled = _with_info(
        base, CreationDate="D:20210708145315+00'00'", ModDate="D:20210708150155+02'00'"
    )
    assert _severity(mislabelled, "TL-META-003") == set()
    reversed_dates = _with_info(
        base, CreationDate="D:20210708145315+00'00'", ModDate="D:20150917162633+00'00'"
    )
    assert _severity(reversed_dates, "TL-META-003") == {"medium"}


def test_redrawn_text_and_logo_lettering_are_not_replacements() -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, 760, "PASIVOS MONEDA EXTRANJERA")
        c.setFillColorRGB(1, 1, 1)
        c.rect(48, 757, 200, 14, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(50, 760, "PASIVOS MONEDA EXTRANJERA")  # the same text, drawn again
        c.drawString(50, 700, "hRTOUOTet24")
        c.drawString(50, 700, "PARTOUT 24h")  # stylised lettering drawn over lettering

    assert _severity(pdf(draw), "TL-OVL-001") <= {"low"}


def test_bold_totals_are_styling_not_a_foreign_font() -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(50, 780, "Harbour City Bank - Account Statement")
        for i in range(12):
            c.drawString(50, 740 - 16 * i, f"0{i % 9 + 1} Mar 2026  Grocery Mart  card payment")
            c.drawRightString(500, 740 - 16 * i, f"{100 + i:,.2f}")
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, 520, "Total")
        c.drawRightString(500, 520, "1,266.00")

    assert "TL-FONT-002" not in rules_of(pdf(draw))


def test_template_offsets_repeat_across_pages() -> None:
    """A report template that sets one column 0.9 pt low does so on every page."""

    def draw(c: Canvas) -> None:
        for page in range(3):
            c.setFont("Helvetica", 10)
            c.drawString(50, 780, f"Quarterly report - page {page + 1}")
            for i in range(4):
                y = 740 - 18 * i
                c.drawString(50, y, f"0{i + 1} Mar 2026")
                c.drawString(150, y, "Grocery Mart")
                c.drawRightString(400, y - (0.9 if i == 2 else 0), "1,000.00")
                c.drawRightString(500, y, f"{9_000 - 37 * i:,.2f}")
            c.showPage()

    assert "TL-GEO-001" not in rules_of(pdf(draw))


def test_font_used_on_other_pages_is_the_documents_own() -> None:
    """Report engines often set number cells in their own font; a page with few numbers
    then uses it for only a handful of digits."""

    def draw(c: Canvas) -> None:
        c.setFont("Times-Roman", 10)
        c.drawString(50, 780, "Annual report - figures")
        for i in range(20):
            c.setFont("Times-Roman", 10)
            c.drawString(50, 740 - 16 * i, "Expense line with a long description of the item")
            c.setFont("Helvetica", 10)
            c.drawRightString(500, 740 - 16 * i, f"{1_000 + 37 * i:,.2f}")
        c.showPage()
        c.setFont("Times-Roman", 10)
        for i in range(12):
            c.drawString(50, 740 - 16 * i, "Commentary on the figures presented on the page before")
        c.setFont("Helvetica", 10)
        c.drawRightString(500, 500, "1,250.00")
        c.drawRightString(500, 484, "2,500.00")

    assert "TL-FONT-002" not in rules_of(pdf(draw))


def test_libreoffice_numbered_subsets_are_one_export() -> None:
    """LibreOffice numbers its subsets (BAAAAA, CAAAAA, ...) and may split one font across
    several of them in a single export, repeating glyphs; an editor's subset stands out."""
    page = Page(number=1, width=595, height=842)
    page.chars = [
        Char(t, "CAAAAA+Verdana", 10, i, i + 5, 100, 110) for i, t in enumerate("Sum 1.0")
    ]
    page.chars += [
        Char(t, "EAAAAA+Verdana", 7, i, i + 4, 800, 807) for i, t in enumerate("SPEC 1.0")
    ]
    doc = Document(name="x.pdf", data=b"", sha256="", kind="pdf", pages=[page])
    assert FontDetector()._subsets(doc) == []
    page.chars.append(Char("1", "QWXTZK+Verdana", 10, 60, 65, 140, 150))
    assert [f.rule_id for f in FontDetector()._subsets(doc)] == ["TL-FONT-001"]


def test_page_number_in_its_own_font_is_not_flagged() -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        for i in range(30):
            c.drawString(50, 780 - i * 20, f"Line {i} of an ordinary genuine letter body text.")
        c.setFont("Times-Roman", 9)
        c.drawCentredString(297, 30, "- 1 -")

    assert "TL-FONT-002" not in rules_of(pdf(draw))


def test_translucent_highlight_does_not_hide_text() -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(50, 700, "Amount payable 1,234.56")
        c.setFillColorRGB(1, 1, 0)
        c.setFillAlpha(0.35)
        c.rect(45, 695, 200, 16, fill=1, stroke=0)

    assert "TL-OVL-001" not in rules_of(pdf(draw))


# --------------------------------------------------------------------------- structure


def _incremental(data: bytes, edit: Callable[[IncrementalPdfFileWriter], None]) -> bytes:
    writer = IncrementalPdfFileWriter(io.BytesIO(data))
    edit(writer)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _draw_on_page(page_index: int) -> Callable[[IncrementalPdfFileWriter], None]:
    def edit(writer: IncrementalPdfFileWriter) -> None:
        ref = writer.add_object(
            generic.StreamObject(stream_data=b"q 1 1 1 rg 100 100 50 10 re f Q")
        )
        writer.add_stream_to_page(page_index, ref)

    return edit


def test_malformed_font_entry_does_not_hide_an_edit() -> None:
    base = io.BytesIO()
    with pikepdf.open(io.BytesIO(multipage_statement())) as pdf:
        pdf.pages[0].obj.Resources.Font["/F99"] = 5  # a non-dictionary font entry
        pdf.save(base)
    edited = _incremental(base.getvalue(), _draw_on_page(1))
    report = check(edited, name="edited.pdf")
    assert any(f.rule_id == "TL-REV-002" and f.page == 2 for f in report.findings)
    assert all(r.status != "error" for r in report.detectors)


def test_page_appended_in_a_later_revision() -> None:
    def append(writer: IncrementalPdfFileWriter) -> None:
        content = writer.add_object(
            generic.StreamObject(stream_data=b"BT /F1 9 Tf 50 700 Td (x) Tj ET")
        )
        page = generic.DictionaryObject(
            {
                generic.NameObject("/Type"): generic.NameObject("/Page"),
                generic.NameObject("/MediaBox"): generic.ArrayObject(
                    [generic.NumberObject(v) for v in (0, 0, 595, 842)]
                ),
                generic.NameObject("/Contents"): content,
                generic.NameObject("/Resources"): generic.DictionaryObject(),
            }
        )
        writer.insert_page(page)

    report = check(_incremental(make_statement(1)[0], append), name="appended.pdf")
    assert "TL-REV-004" in {f.rule_id for f in report.findings}


def test_linearized_file_with_an_update_has_two_revisions() -> None:
    from tamperlint.loaders import load_bytes

    data = benign.add_sticky_note(benign.linearize(make_statement(1)[0]))
    assert len(load_bytes(data, "x.pdf").revisions) == 2


def test_eof_markers_inside_an_attachment_are_not_revisions() -> None:
    inner = benign.sign(make_statement(2)[0])  # has two %%EOF markers of its own
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(make_statement(1)[0])) as pdf:
        pdf.attachments["inner.pdf"] = pikepdf.AttachedFileSpec(pdf, inner)
        pdf.save(out, compress_streams=False)
    data = out.getvalue()
    assert data.count(b"%%EOF") >= 3
    report = check(data, name="with-attachment.pdf")
    assert report.verdict is Verdict.INTACT, [(f.rule_id, f.message) for f in report.findings]


def test_altered_signature_value_is_not_intact() -> None:
    signed = bytearray(benign.sign(make_statement(1)[0]))
    m = re.search(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", signed)
    assert m
    pos = int(m.group(1)) + int(m.group(2)) + 200  # inside the hex signature value
    signed[pos] = ord("0") if signed[pos] != ord("0") else ord("1")
    rules = {f.rule_id for f in check(bytes(signed), name="s.pdf").findings}
    assert "TL-SIG-003" in rules and "TL-SIG-001" not in rules


def test_certification_violation_is_reported() -> None:
    from pyhanko.sign import signers
    from pyhanko.sign.fields import MDPPerm
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    cert, key = benign._self_signed()
    signer = signers.SimpleSigner(
        signing_cert=cert, signing_key=key, cert_registry=SimpleCertificateStore()
    )
    meta = signers.PdfSignatureMetadata(
        field_name="Cert", certify=True, docmdp_permissions=MDPPerm.NO_CHANGES
    )
    certified = signers.sign_pdf(
        IncrementalPdfFileWriter(io.BytesIO(make_statement(1)[0])), meta, signer=signer
    ).getvalue()
    report = check(benign.add_sticky_note(certified), name="certified.pdf")
    sig = [f for f in report.findings if f.rule_id == "TL-SIG-002"]
    assert sig and sig[0].severity.value == "medium"
    assert report.verdict is not Verdict.INTACT


def test_signatures_are_checked_inside_a_running_event_loop() -> None:
    signed = benign.sign(make_statement(1)[0])

    async def run() -> set[str]:
        return {f.rule_id for f in check(signed, name="s.pdf").findings}

    assert "TL-SIG-001" in asyncio.run(run())


def test_malformed_text_render_operand_does_not_crash() -> None:
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(make_statement(1)[0])) as pdf:
        pdf.pages[0].contents_add(pikepdf.Stream(pdf, b"BT /Foo Tr ET"), prepend=False)
        pdf.save(out)
    assert check(out.getvalue(), name="odd.pdf").verdict is Verdict.INTACT


def test_zero_page_pdf_is_inconclusive() -> None:
    out = io.BytesIO()
    pikepdf.new().save(out)
    assert check(out.getvalue(), name="empty.pdf").verdict is Verdict.INCONCLUSIVE


def test_password_protected_pdf_is_rejected_cleanly() -> None:
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(make_statement(1)[0])) as pdf:
        pdf.save(out, encryption=pikepdf.Encryption(user="secret", owner="owner"))
    with pytest.raises(UnsupportedFileError, match="encrypted with a password"):
        check(out.getvalue(), name="locked.pdf")


def test_page_count_is_reported_beyond_the_analysis_limit() -> None:
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(make_statement(1)[0])) as pdf:
        for _ in range(204):
            pdf.pages.append(pdf.pages[0])
        pdf.save(out)
    report = check(out.getvalue(), name="long.pdf")
    assert report.file.pages == 205
    assert any("first 50 of 205 pages" in item for item in report.limitations)
    assert any("first 200 of 205 pages" in item for item in report.limitations)


def test_edit_markers_are_reported_beyond_the_text_pages() -> None:
    """Content streams are scanned further than text is laid out, so an Acrobat edit on page
    55 of a 60-page file is still reported."""
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(make_statement(1)[0])) as doc:
        for _ in range(59):
            doc.pages.append(doc.pages[0])
        doc.pages[54].contents_add(pikepdf.Stream(doc, b"/TouchUp_TextEdit MP"), prepend=False)
        doc.save(out)
    found = [f for f in check(out.getvalue()).findings if f.rule_id == "TL-EDIT-001"]
    assert [f.page for f in found] == [55]


# --------------------------------------------------------------------------- images


def test_tiny_image_does_not_crash() -> None:
    report = check(raster.jpeg(np.full((400, 80), 200, np.uint8), 80), name="tiny.jpg")
    assert all(r.status != "error" for r in report.detectors)


def test_smooth_gradient_is_not_mistaken_for_splicing() -> None:
    h, w = 1200, 900
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vignette = np.clip(200 - 140 * (((xx - w / 2) / w) ** 2 + ((yy - h / 2) / h) ** 2), 0, 255)
    report = check(raster.jpeg(vignette.astype(np.uint8), 92), name="vignette.jpg")
    assert "TL-IMG-001" not in {f.rule_id for f in report.findings}


# --------------------------------------------------------------------------- printed tables
# Geometry measured from documents printed to PDF by Chrome and LibreOffice.

BROWSER_ROWS = [
    ("01 Mar 2026", "Electric Utility", "12,838.50", "", "92,251.19"),
    ("03 Mar 2026", "Grocery Mart", "9,488.78", "", "82,762.41"),
    ("07 Mar 2026", "Salary", "", "41,292.96", "124,055.37"),
    ("09 Mar 2026", "Book House", "1,055.37", "", "123,000.00"),
]


def _browser_statement(
    rows: list[tuple[str, str, str, str, str]], *, double_strike: bool = False
) -> bytes:
    """Headers start at their column's left edge; numbers end at its right edge, just short
    of the next header, as browsers print HTML tables."""

    def draw(c: Canvas) -> None:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(54, 790, "Harbour City Bank")
        c.setFont("Helvetica", 10)
        c.drawString(54, 772, "Account Statement")
        c.drawString(54, 740, "Opening balance 105,089.69")
        c.setFont("Helvetica-Bold", 10)
        for x, text in ((54, "Date"), (155, "Description"), (288, "Debit"), (371, "Credit")):
            c.drawString(x, 700, text)
        c.drawString(453, 700, "Balance")
        c.setFont("Helvetica", 10)
        y = 682.0
        for date, desc, debit, credit, balance in rows:
            c.drawString(54, y, date)
            c.drawString(155, y, desc)
            for x, value in ((365, debit), (448, credit), (541, balance)):
                if value:
                    c.drawRightString(x, y, value)
                    if double_strike:  # some report engines embolden text by printing it twice
                        c.drawRightString(x + 0.3, y, value)
            y -= 18
        c.drawString(54, y - 8, "Closing balance")
        c.drawRightString(541, y - 8, rows[-1][4])

    return pdf(draw)


@pytest.mark.parametrize("double_strike", [False, True])
def test_left_aligned_headers_over_right_aligned_numbers(double_strike: bool) -> None:
    report = check(_browser_statement(BROWSER_ROWS, double_strike=double_strike), name="s.pdf")
    assert report.doc_type == "statement"
    assert report.verdict is Verdict.INTACT, [(f.rule_id, f.message) for f in report.findings]
    tampered = [BROWSER_ROWS[0], (*BROWSER_ROWS[1][:4], "83,762.41"), *BROWSER_ROWS[2:]]
    found = [
        f
        for f in check(_browser_statement(tampered, double_strike=double_strike)).findings
        if f.rule_id == "TL-LOGIC-001"
    ]
    assert found and "debit 9,488.78" in found[0].message  # read from the right column


def _tax_inclusive_invoice(total_shift: float = 0.0) -> bytes:
    """Right-aligned multi-word headers ("Price (incl. GST)") and a tax-inclusive total."""
    items = [("Printer paper", 4, 1_096.52), ("Chair mat", 9, 8_627.48), ("Cable set", 6, 5_784.03)]
    total = round(sum(qty * price for _, qty, price in items), 2)

    def draw(c: Canvas) -> None:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(47, 790, "Contoso Office Mart")
        c.setFont("Helvetica", 10)
        c.drawString(47, 772, "Tax Invoice    Invoice No. INV-48213")
        c.setFont("Helvetica-Bold", 9)
        c.drawString(47, 740, "Item")
        for x, text in ((150, "Qty"), (260, "Price (incl. GST)"), (340, "Amount")):
            c.drawRightString(x, 740, text)
        c.setFont("Helvetica", 9)
        y = 724.0
        for name, qty, price in items:
            c.drawString(47, y, name)
            c.drawRightString(150, y, str(qty))
            c.drawRightString(260, y, f"{price:,.2f}")
            c.drawRightString(340, y, f"{qty * price:,.2f}")
            y -= 16
        for label, value in (
            ("Total (incl. GST)", total + total_shift),
            ("Includes GST 17%", round(total - total / 1.17, 2)),
        ):
            c.drawRightString(260, y, label)
            c.drawRightString(340, y, f"{value:,.2f}")
            y -= 16

    return pdf(draw)


def test_tax_inclusive_invoice_with_multi_word_headers() -> None:
    report = check(_tax_inclusive_invoice(), name="invoice.pdf")
    assert report.doc_type == "invoice"
    assert report.verdict is Verdict.INTACT, [(f.rule_id, f.message) for f in report.findings]
    assert "TL-LOGIC-002" in rules_of(_tax_inclusive_invoice(total_shift=5_000))


def test_retyped_majority_of_a_column_is_still_the_odd_one_out() -> None:
    """A forger who retypes every later balance leaves the original font in the minority of
    the balance column; the page's own font still tells which values are foreign."""

    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(54, 790, "Harbour City Bank - Account Statement")
        c.drawString(54, 760, "Opening balance 10,000.00")
        for x, text in ((54, "Date"), (155, "Description"), (365, "Debit"), (541, "Balance")):
            (c.drawString if x < 300 else c.drawRightString)(x, 720, text)
        y = 700.0
        for i in range(9):
            c.setFont("Helvetica", 10)
            c.drawString(54, y, f"0{i + 1} Mar 2026")
            c.drawString(155, y, "Grocery Mart")
            c.drawRightString(365, y, "100.00")
            c.setFont("Courier" if i >= 3 else "Helvetica", 10)
            c.drawRightString(541, y, f"{10_000 - 100 * (i + 1):,.2f}")
            y -= 18

    findings = [f for f in check(pdf(draw)).findings if f.rule_id == "TL-GEO-002"]
    assert len(findings) == 6 and all("Courier" in f.evidence["font"] for f in findings)


# --------------------------------------------------------------------------- synthetic corpus


def test_genuine_statements_look_plausible() -> None:
    for seed in range(50):
        _, truth = make_statement(seed)
        assert min(row.balance for row in truth.rows) >= MIN_BALANCE, seed  # never overdrawn
        assert not any(row.credit and row.description in MERCHANTS for row in truth.rows), seed


def test_demo_samples_show_no_negative_amounts() -> None:
    """The samples appear in the README and the demo; a savings account below zero looks fake."""
    for name, data in build_samples().items():
        if name.endswith(".pdf"):
            with pdfplumber.open(io.BytesIO(data)) as doc:
                text = "\n".join(page.extract_text() or "" for page in doc.pages)
            assert not re.search(r"(?<![\w-])-\s?\d[\d,]*\.\d\d\b", text), name  # not INV-59321


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_forged_cover_boxes_hide_every_original_glyph(seed: int) -> None:
    """A careful forger leaves no digit of the old value peeking out from under the box."""
    data, truth = make_statement(seed)
    assert truth.closing_cell is not None
    cells = [c for row in truth.rows for c in row.cells.values() if c.text]
    cells.append(truth.closing_cell)
    page = load_bytes(
        forge.overlay_edit(data, [forge.Replacement(c, "1.00") for c in cells]), "forged.pdf"
    ).pages[0]
    for cell in cells:
        width = pdfmetrics.stringWidth(cell.text, cell.font, cell.size)
        left = [
            ch.text
            for ch in page.chars
            if ch.upright
            and not ch.hidden
            and "Arial" not in ch.fontname
            and abs(ch.baseline - (page.height - cell.y)) < 1
            and cell.x_right - width - 0.5 < (ch.x0 + ch.x1) / 2 < cell.x_right + 0.5
        ]
        assert not left, f"{cell.text!r} still shows {''.join(left)!r}"


# --------------------------------------------------------------------------- plugins


def test_plugins_register_their_own_rules() -> None:
    rule = RULES["TL-LOGIC-004"]
    custom = Rule("ACME-PAY-001", "Payslip tax mismatch", rule.layer, rule.severity, "d", "fp")
    assert register_rule(custom) is custom and RULES["ACME-PAY-001"] == custom
    assert register_rule(custom) is custom  # idempotent
    with pytest.raises(ValueError, match="reserved"):
        register_rule(Rule("TL-NEW-001", "x", rule.layer, rule.severity, "d", "fp"))
    del RULES["ACME-PAY-001"]

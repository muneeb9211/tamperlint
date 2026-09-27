"""Forging techniques used to build the evaluation corpus.

Each function reproduces a technique seen in real document fraud, applied to our own synthetic
documents. They are intentionally simple and documented so that anyone can audit what the
evaluation measures.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from reportlab.pdfbase import pdfmetrics

from tamperlint.synth.genuine import Cell, StatementTruth, money, register_fonts


@dataclass
class Replacement:
    cell: Cell
    new_text: str


def _font_dict(writer: IncrementalPdfFileWriter, font: str) -> generic.DictionaryObject:
    """A non-embedded font reference with real metrics, as desktop PDF editors write it.

    Arial and Helvetica share glyph widths, so Helvetica's metrics stand in for ArialMT.
    """
    name = generic.NameObject
    if font in pdfmetrics.standardFonts:
        return generic.DictionaryObject(
            {
                name("/Type"): name("/Font"),
                name("/Subtype"): name("/Type1"),
                name("/BaseFont"): name(f"/{font}"),
                name("/Encoding"): name("/WinAnsiEncoding"),
            }
        )
    widths = pdfmetrics.getFont("Helvetica").widths
    descriptor = writer.add_object(
        generic.DictionaryObject(
            {
                name("/Type"): name("/FontDescriptor"),
                name("/FontName"): name(f"/{font}"),
                name("/Flags"): generic.NumberObject(32),
                name("/FontBBox"): generic.ArrayObject(
                    [generic.NumberObject(v) for v in (-665, -325, 2000, 1040)]
                ),
                name("/ItalicAngle"): generic.NumberObject(0),
                name("/Ascent"): generic.NumberObject(905),
                name("/Descent"): generic.NumberObject(-212),
                name("/CapHeight"): generic.NumberObject(716),
                name("/StemV"): generic.NumberObject(80),
            }
        )
    )
    return generic.DictionaryObject(
        {
            name("/Type"): name("/Font"),
            name("/Subtype"): name("/TrueType"),
            name("/BaseFont"): name(f"/{font}"),
            name("/Encoding"): name("/WinAnsiEncoding"),
            name("/FirstChar"): generic.NumberObject(32),
            name("/LastChar"): generic.NumberObject(255),
            name("/Widths"): generic.ArrayObject(
                [generic.NumberObject(int(w)) for w in widths[32:256]]
            ),
            name("/FontDescriptor"): descriptor,
        }
    )


def _escape(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def overlay_edit(
    data: bytes,
    replacements: list[Replacement],
    *,
    font: str = "ArialMT",
    size_delta: float = 0.0,
    baseline_shift: float = 0.0,
    cover: bool = True,
    touchup: bool = False,
    producer: str | None = None,
    page_index: int = 0,
) -> bytes:
    """Cover values with white boxes and draw replacement text, as an incremental update.

    This mimics the most common manual forgery: open the PDF in an editor, hide the original
    number and type a new one on top.
    """
    register_fonts()
    writer = IncrementalPdfFileWriter(io.BytesIO(data))
    font_ref = writer.add_object(_font_dict(writer, font))
    ops: list[bytes] = []
    for rep in replacements:
        size = rep.cell.size + size_delta
        # Measured in the font the value was drawn in, so the box hides every original glyph.
        old_w = pdfmetrics.stringWidth(rep.cell.text, rep.cell.font, rep.cell.size)
        metrics_font = (
            font if font in pdfmetrics.standardFonts else "Helvetica"
        )  # Arial ~ Helvetica
        new_w = pdfmetrics.stringWidth(rep.new_text, metrics_font, size)
        if cover:
            x0 = rep.cell.x_right - max(old_w, new_w) - 2
            ops.append(
                f"q 1 1 1 rg {x0:.2f} {rep.cell.y - 2.5:.2f} {max(old_w, new_w) + 4:.2f} "
                f"{rep.cell.size + 3:.2f} re f Q".encode()
            )
        text_op = (
            f"BT /TLF1 {size:.2f} Tf 0 0 0 rg {rep.cell.x_right - new_w:.2f} "
            f"{rep.cell.y + baseline_shift:.2f} Td (".encode()
            + _escape(rep.new_text)
            + b") Tj ET"
        )
        if touchup:
            text_op = b"/TouchUp_TextEdit MP " + text_op
        ops.append(text_op)
    stream_ref = writer.add_object(generic.StreamObject(stream_data=b"\n".join(ops)))
    resources = generic.DictionaryObject(
        {
            generic.NameObject("/Font"): generic.DictionaryObject(
                {generic.NameObject("/TLF1"): font_ref}
            )
        }
    )
    writer.add_stream_to_page(page_index, stream_ref, resources=resources)
    if producer:
        writer.set_info(
            generic.DictionaryObject(
                {
                    generic.NameObject("/Producer"): generic.TextStringObject(producer),
                    generic.NameObject("/ModDate"): generic.TextStringObject(
                        "D:20260915101500+05'00'"
                    ),
                }
            )
        )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def statement_amount_edit(
    data: bytes,
    truth: StatementTruth,
    row: int,
    new_amount: float,
    *,
    recalc_balances: bool = False,
    **overlay_kwargs: object,
) -> bytes:
    """Change one debit or credit on a statement.

    With ``recalc_balances`` the forger also fixes every later balance and the closing balance,
    so the arithmetic stays consistent and only structural traces remain.
    """
    target = truth.rows[row]
    field_name = "credit" if target.credit else "debit"
    old = getattr(target, field_name)
    reps = [Replacement(target.cells[field_name], money(new_amount))]
    if recalc_balances:
        delta = (new_amount - old) if field_name == "credit" else (old - new_amount)
        for later in truth.rows[row:]:
            reps.append(Replacement(later.cells["balance"], money(later.balance + delta)))
        assert truth.closing_cell is not None
        reps.append(Replacement(truth.closing_cell, money(truth.closing_balance + delta)))
    return overlay_edit(data, reps, **overlay_kwargs)  # type: ignore[arg-type]

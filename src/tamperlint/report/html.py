"""Self-contained HTML report with page previews and highlighted findings."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from importlib.resources import files

from jinja2 import Environment, select_autoescape

from tamperlint.models import Report, Severity
from tamperlint.report.grouping import group_findings

PREVIEW_DPI = 90
MAX_PREVIEW_PAGES = 12


@dataclass
class Preview:
    page: int
    width: float
    height: float
    png_b64: str


def _render_previews(data: bytes, kind: str, pages: list[int]) -> list[Preview]:
    """Render the pages that carry findings (plus page 1) to small PNG previews."""
    from PIL import Image

    previews: list[Preview] = []
    if kind == "image":
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            thumb = img.convert("RGB")
        thumb.thumbnail((1100, 1400))
        buf = io.BytesIO()
        thumb.save(buf, format="PNG", optimize=True)
        return [Preview(1, float(width), float(height), base64.b64encode(buf.getvalue()).decode())]

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(io.BytesIO(data))
    try:
        for number in pages[:MAX_PREVIEW_PAGES]:
            if number > len(pdf):
                continue
            page = pdf[number - 1]
            width, height = page.get_size()
            pil = page.render(scale=PREVIEW_DPI / 72).to_pil().convert("RGB")
            buf = io.BytesIO()
            pil.save(buf, format="PNG", optimize=True)
            previews.append(
                Preview(
                    number, float(width), float(height), base64.b64encode(buf.getvalue()).decode()
                )
            )
    finally:
        pdf.close()
    return previews


def render_html(report: Report, data: bytes | None = None) -> str:
    """Render ``report`` as one HTML file. Pass the original bytes to include page previews."""
    pages = sorted({1} | {f.page for f in report.findings if f.page})
    previews: list[Preview] = []
    preview_error = None
    if data is not None:
        try:
            previews = _render_previews(data, report.file.kind, pages)
        except Exception as exc:
            preview_error = f"Page previews could not be rendered: {exc}"
    env = Environment(autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    template = env.from_string(
        files("tamperlint.report").joinpath("templates/report.html.j2").read_text(encoding="utf-8")
    )
    groups = list(enumerate(group_findings(report.findings), start=1))
    return template.render(
        report=report,
        groups=groups,
        previews=previews,
        preview_error=preview_error,
        severity_order=[Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO],
    )

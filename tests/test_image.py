"""Pixel-level checks on synthetic scans."""

from __future__ import annotations

import numpy as np
import pytest

from tamperlint import Verdict, check
from tamperlint.detectors.image.jpeg_grid import JpegGridDetector
from tamperlint.document import BBox, Document, Raster
from tamperlint.synth import raster
from tamperlint.synth.genuine import make_invoice, make_statement

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def scans() -> dict[str, bytes]:
    pdf, truth = make_statement(1)
    gray = raster.scan(raster.render(pdf, dpi=150), seed=1)
    h, w = gray.shape
    host = raster.jpeg(gray, 80)
    cell = truth.rows[3].cells.get("credit") or truth.rows[3].cells["debit"]
    s = 150 / 72
    box = (
        int((cell.x_right - 70) * s) - 60,
        int((truth.page_height - cell.y - 12) * s) - 40,
        int((cell.x_right + 4) * s) + 60,
        int((truth.page_height - cell.y + 5) * s) + 40,
    )
    donor = raster.scan(raster.render(make_statement(7)[0], dpi=150), seed=2)
    block = (80, int(h * 0.35), w - 80, int(h * 0.35) + 110)
    return {
        "genuine.jpg": host,
        "genuine.png": raster.png(raster.decode(host)),
        "splice.png": raster.png(raster.splice(host, donor, box)),
        "copymove.jpg": raster.jpeg(
            raster.copy_move(raster.decode(host), block, (80, int(h * 0.62))), 90
        ),
    }


def test_genuine_scans_have_no_findings(scans: dict[str, bytes]) -> None:
    for name in ("genuine.jpg", "genuine.png"):
        report = check(scans[name], name=name)
        assert report.findings == [], name
        # Pixel checks alone cannot vouch for a document.
        assert report.verdict is Verdict.INCONCLUSIVE


def test_splice_is_localised(scans: dict[str, bytes]) -> None:
    report = check(scans["splice.png"], name="splice.png")
    grid = [f for f in report.findings if f.rule_id == "TL-IMG-001"]
    assert grid and grid[0].bbox is not None


def test_sideways_repeat_is_a_hint() -> None:
    """Letterheads and forms repeat logos, stamps and boxes along a line; a stamp or rows
    copied to another place on the page move down."""
    rng = np.random.default_rng(7)
    page = np.full((1600, 1200), 245, np.uint8)
    for _ in range(2500):  # glyph-like marks, different everywhere
        y, x = rng.integers(0, 1590), rng.integers(0, 1190)
        page[y : y + rng.integers(3, 9), x : x + rng.integers(2, 7)] = rng.integers(20, 90)
    ink = (rng.random((180, 220)) * 80).astype(np.uint8)
    stamp = np.where(rng.random((180, 220)) > 0.6, ink, 245).astype(np.uint8)
    severities = {}
    for label, (y, x) in (("sideways", (200, 760)), ("down", (900, 120))):
        img = page.copy()
        img[200:380, 120:340] = stamp
        img[y : y + 180, x : x + 220] = stamp
        report = check(raster.jpeg(img, 92), name="scan.jpg")
        severities[label] = {f.severity.value for f in report.findings if f.rule_id == "TL-IMG-002"}
    assert severities == {"sideways": {"low"}, "down": {"high"}}


def test_grid_evidence_is_a_hint_inside_pdfs(scans: dict[str, bytes]) -> None:
    """Real scans embedded in PDFs are often recompressed in parts when the PDF is made, so
    a grid mismatch there must not set the verdict; in an image file it stays evidence."""
    gray = raster.decode(scans["splice.png"])
    doc = Document(name="x.pdf", data=b"", sha256="", kind="pdf")
    box = BBox(x0=0, top=0, x1=595, bottom=842)
    doc.rasters = [Raster(page=1, gray=gray, bbox=box, source="embedded", jpeg=True)]
    embedded = JpegGridDetector().run(doc)
    doc.rasters = [Raster(page=1, gray=gray, bbox=None, source="file", jpeg=False)]
    direct = JpegGridDetector().run(doc)
    assert embedded and {f.severity.value for f in embedded} == {"low"}
    assert direct and {f.severity.value for f in direct} == {"medium"}


def test_copy_move_detected_once(scans: dict[str, bytes]) -> None:
    report = check(scans["copymove.jpg"], name="copymove.jpg")
    copies = [f for f in report.findings if f.rule_id == "TL-IMG-002"]
    assert len(copies) == 1
    assert report.verdict is Verdict.SUSPICIOUS


@pytest.mark.parametrize("seed", [3, 4, 5, 6, 12, 21])
def test_no_false_positives_across_layouts(seed: int) -> None:
    for make in (make_statement, make_invoice):
        gray = raster.scan(raster.render(make(seed)[0], dpi=150), seed=seed)
        for quality in (70, 85):
            report = check(raster.jpeg(gray, quality), name="scan.jpg")
            assert report.findings == [], (make.__name__, seed, quality)

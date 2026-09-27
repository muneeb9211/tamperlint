"""TL-OVL: content hidden under shapes, annotations or invisible text."""

from __future__ import annotations

from tamperlint.detectors.base import Detector
from tamperlint.document import Char, Document, Page
from tamperlint.models import BBox, Finding, Layer, Severity
from tamperlint.text import CharIndex, visible_chars
from tamperlint.util import overlap_ratio

_OVERLAY_ANNOTS = {"FreeText", "Square", "Stamp", "Circle", "Polygon", "Ink"}
MAX_REPLACED_CHARS = 40  # a retyped field, not a hidden block of text
PATCH_AREA = 8.0  # a correction patch covers at most this multiple of the text it hides


def _readable(ch: Char) -> bool:
    """Real text: not whitespace, and not a glyph without a real Unicode mapping (replacement
    characters, "(cid:N)" placeholders, private-use code points of symbol and ligature fonts)."""
    text = ch.text
    if not text.strip() or text == "\ufffd" or text.startswith("(cid:"):
        return False
    return not any(0xE000 <= ord(c) <= 0xF8FF for c in text)


def _amount_like(text: str) -> bool:
    """At least three digits, and little else: 1,250.00 or 350, not 24h or t24."""
    digits = sum(c.isdigit() for c in text)
    return digits >= 3 and digits + sum(c in ".,-" for c in text) >= 0.8 * len(text)


def _patch(cluster: list[Char], box: BBox) -> bool:
    """The shapes hiding these characters hug them, like a correction patch.

    Slides and designed pages put panels and pictures over text; those cover far more than
    the text they hide.
    """
    covers = {c.cover for c in cluster if c.cover is not None}
    if not covers:
        return True
    text_area = max(box.width * box.height, 1.0)
    return all(
        (x1 - x0) * (bottom - top) <= PATCH_AREA * text_area
        and bottom - top <= 3 * max(box.height, 1.0)
        for x0, top, x1, bottom in covers
    )


def _replaced(old: str, new: str, cluster: list[Char], box: BBox) -> bool:
    """A short piece of text hidden and a similar piece drawn in its place: a retyped field.

    Designed pages also layer text under shapes (placeholders, running footers, art), but they
    do not patch it out and put similar text back in the same spot. Text typed straight over
    text only counts for amounts, since letters and short numbers in logos, footnotes and tight
    headings overlap by design.
    """
    if not 2 <= len(old) <= MAX_REPLACED_CHARS or not new or new == old:
        return False  # the same text drawn again (for emphasis, or in two passes) changes nothing
    if not 0.5 <= len(new) / len(old) <= 2:
        return False
    if cluster[0].hidden_by == "text":
        return _amount_like(old) and _amount_like(new)
    return _patch(cluster, box)


def _cluster(chars: list[Char], gap: float = 6.0) -> list[list[Char]]:
    clusters: list[list[Char]] = []
    for ch in sorted(chars, key=lambda c: (round(c.top), c.x0)):
        for cl in clusters:
            last = cl[-1]
            if abs(ch.top - last.top) < 2 and ch.x0 - last.x1 < gap:
                cl.append(ch)
                break
        else:
            clusters.append([ch])
    return clusters


def _box(chars: list[Char]) -> BBox:
    return BBox(
        x0=min(c.x0 for c in chars),
        top=min(c.top for c in chars),
        x1=max(c.x1 for c in chars),
        bottom=max(c.bottom for c in chars),
    )


class OverlayDetector(Detector):
    name = "overlays"
    layer = Layer.CONTENT
    rule_ids = ("TL-OVL-001", "TL-OVL-002", "TL-OVL-003")

    def applies(self, doc: Document) -> str | None:
        return None if doc.kind == "pdf" else "not a PDF"

    def run(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        for page in doc.pages:
            findings += self._hidden_text(page)
            findings += self._annotations(page)
            if page.invisible_chars and not page.has_full_page_image:
                findings.append(
                    self.finding(
                        "TL-OVL-003",
                        f"Page {page.number} draws {page.invisible_chars} text run(s) in invisible "
                        "mode, but has no scanned image underneath, so this is not an OCR layer.",
                        confidence=0.5,
                        page=page.number,
                        evidence={"invisible_runs": page.invisible_chars},
                    )
                )
        return findings

    def _hidden_text(self, page: Page) -> list[Finding]:
        findings: list[Finding] = []
        hidden = [c for c in page.chars if c.hidden and _readable(c)]
        if not hidden:
            return findings
        shown = CharIndex(c for c in visible_chars(page) if _readable(c))
        for cluster in _cluster(hidden):
            box = _box(cluster)
            old = "".join(c.text for c in cluster)
            area = (box.x0 - 1, box.top - 1, box.x1 + 1, box.bottom + 1)
            latest = max(h.order for h in cluster)
            on_top = [
                c
                for c in shown.near(area[1], area[3])
                if c.order > latest and overlap_ratio((c.x0, c.top, c.x1, c.bottom), area) > 0.5
            ]
            new = "".join(c.text for c in sorted(on_top, key=lambda c: c.x0))
            how = (
                "covered by a filled shape"
                if cluster[0].hidden_by == "shape"
                else "overprinted by other text"
            )
            message = f"On page {page.number}, the text '{old}' is {how}"
            message += f", and '{new}' is drawn on top of it." if new else "."
            replaced = _replaced(old, new, cluster, box)
            findings.append(
                self.finding(
                    "TL-OVL-001",
                    message,
                    confidence=0.85 if replaced else 0.55,
                    severity=None if replaced else Severity.LOW,
                    page=page.number,
                    bbox=box,
                    evidence={
                        "hidden_text": old,
                        "visible_text": new or None,
                        "hidden_by": cluster[0].hidden_by,
                    },
                )
            )
        return findings

    def _annotations(self, page: Page) -> list[Finding]:
        findings: list[Finding] = []
        shown = visible_chars(page)
        for annot in page.annotations:
            if annot.subtype not in _OVERLAY_ANNOTS:
                continue
            rect = (annot.x0, annot.top, annot.x1, annot.bottom)
            under = [c for c in shown if overlap_ratio((c.x0, c.top, c.x1, c.bottom), rect) > 0.5]
            if len(under) < 3:
                continue
            text = "".join(c.text for c in sorted(under, key=lambda c: (round(c.top), c.x0)))[:60]
            findings.append(
                self.finding(
                    "TL-OVL-002",
                    f"A {annot.subtype} annotation on page {page.number} sits on top of the page "
                    f"text '{text}'. Viewers show the annotation instead of the text beneath.",
                    confidence=0.65,
                    page=page.number,
                    bbox=annot.bbox,
                    evidence={
                        "annotation": annot.subtype,
                        "covered_text": text,
                        "contents": annot.contents,
                    },
                )
            )
        return findings

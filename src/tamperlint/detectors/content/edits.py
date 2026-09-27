"""TL-EDIT: explicit editing markers left in content streams."""

from __future__ import annotations

from tamperlint.detectors.base import Detector
from tamperlint.document import Document
from tamperlint.models import Finding, Layer


class EditMarkerDetector(Detector):
    name = "edit-markers"
    layer = Layer.CONTENT
    rule_ids = ("TL-EDIT-001",)

    def applies(self, doc: Document) -> str | None:
        return None if doc.kind == "pdf" else "not a PDF"

    def run(self, doc: Document) -> list[Finding]:
        # The loader scans content streams of more pages than it lays out as text.
        markers = dict(doc.touchup_markers)
        for page in doc.pages:
            if page.touchup_markers:
                markers.setdefault(page.number, page.touchup_markers)
        return [
            self.finding(
                "TL-EDIT-001",
                f"Page {number} contains {count} Acrobat text-edit "
                "marker(s): text on this page was edited by hand in Adobe Acrobat.",
                confidence=0.9,
                page=number,
                evidence={"markers": count},
            )
            for number, count in sorted(markers.items())
            if count
        ]

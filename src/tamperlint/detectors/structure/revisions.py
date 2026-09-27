"""TL-REV: what changed between incremental saves."""

from __future__ import annotations

from tamperlint.detectors.base import Detector
from tamperlint.detectors.structure.signatures import PERMITTED_LEVELS, signed_revision_index
from tamperlint.document import Document, Revision, strip_subset_prefix
from tamperlint.models import Finding, Layer, Severity


class RevisionDetector(Detector):
    name = "revisions"
    layer = Layer.STRUCTURE
    rule_ids = ("TL-REV-001", "TL-REV-002", "TL-REV-003", "TL-REV-004")

    def applies(self, doc: Document) -> str | None:
        return None if doc.kind == "pdf" else "not a PDF"

    def run(self, doc: Document) -> list[Finding]:
        revs = doc.revisions
        findings: list[Finding] = []
        if len(revs) <= 1:
            return findings
        findings.append(
            self.finding(
                "TL-REV-001",
                f"The file contains {len(revs)} revisions: it was saved {len(revs) - 1} more "
                "time(s) after it was first written.",
                confidence=0.95,
                evidence={"revisions": len(revs), "linearized": doc.is_linearized},
            )
        )
        # Font additions are expected from form filling after a *valid* signature that permits
        # it; nothing else downgrades them.
        permitted_after = min(
            (
                idx
                for s in doc.signatures
                if s.intact and s.valid and s.modification_level in PERMITTED_LEVELS
                and (idx := signed_revision_index(doc, s)) is not None
            ),
            default=None,
        )  # fmt: skip
        # Compare every readable revision with the last readable one before it, so an
        # unreadable revision in the middle cannot hide a change.
        previous: Revision | None = None
        for cur in revs:
            if not cur.readable:
                continue
            if previous is not None:
                findings += self._compare(previous, cur, permitted_after)
            previous = cur
        return findings

    def _compare(self, prev: Revision, cur: Revision, permitted_after: int | None) -> list[Finding]:
        findings: list[Finding] = []
        if cur.page_count != prev.page_count:
            change = "added" if cur.page_count > prev.page_count else "removed"
            findings.append(
                self.finding(
                    "TL-REV-004",
                    f"Revision {cur.index} {change} pages: the document had {prev.page_count} "
                    f"page(s) in revision {prev.index} and {cur.page_count} afterwards.",
                    confidence=0.8,
                    revision=cur.index,
                    evidence={"before": prev.page_count, "after": cur.page_count},
                )
            )
        shared = min(len(prev.page_content_hashes), len(cur.page_content_hashes))
        for i in range(shared):
            page_no = i + 1
            if prev.page_content_hashes[i] != cur.page_content_hashes[i]:
                findings.append(
                    self.finding(
                        "TL-REV-002",
                        f"Revision {cur.index} replaced the drawing instructions of page "
                        f"{page_no}, which already existed in revision {prev.index}. The visible "
                        "content of this page was changed after the file was first produced.",
                        confidence=0.9,
                        page=page_no,
                        revision=cur.index,
                        evidence={"previous_revision": prev.index},
                    )
                )
            added = cur.page_fonts[i] - prev.page_fonts[i]
            if not added:
                continue
            names = sorted(added)
            new_bases = {strip_subset_prefix(n) for n in names}
            old_bases = {strip_subset_prefix(n) for n in prev.page_fonts[i]}
            reused_base = bool(new_bases & old_bases)
            permitted = permitted_after is not None and cur.index > permitted_after
            findings.append(
                self.finding(
                    "TL-REV-003",
                    f"Revision {cur.index} added font(s) {', '.join(names)} to page {page_no}."
                    + (
                        " The same typeface already existed under another subset, a typical "
                        "sign of retyped text."
                        if reused_base
                        else ""
                    ),
                    confidence=0.8 if reused_base else 0.65,
                    severity=Severity.LOW if permitted else None,
                    page=page_no,
                    revision=cur.index,
                    evidence={"added_fonts": names, "same_typeface": reused_base},
                )
            )
        return findings

"""TL-SIG: digital-signature integrity and changes made after signing."""

from __future__ import annotations

from tamperlint.detectors.base import Detector
from tamperlint.document import Document, SignatureInfo
from tamperlint.models import Finding, Layer, Severity

PERMITTED_LEVELS = {"LTA_UPDATES", "FORM_FILLING", "ANNOTATIONS"}
_LEVEL_TEXT = {
    "LTA_UPDATES": "long-term validation data",
    "FORM_FILLING": "form filling",
    "ANNOTATIONS": "annotations",
}


class SignatureDetector(Detector):
    name = "signatures"
    layer = Layer.STRUCTURE
    rule_ids = ("TL-SIG-001", "TL-SIG-002", "TL-SIG-003", "TL-SIG-004")

    def applies(self, doc: Document) -> str | None:
        if doc.kind != "pdf":
            return "not a PDF"
        return None if doc.signatures else "document is not digitally signed"

    def run(self, doc: Document) -> list[Finding]:
        return [f for sig in doc.signatures if (f := self._assess(doc, sig)) is not None]

    def _assess(self, doc: Document, sig: SignatureInfo) -> Finding | None:
        ev = {
            "field": sig.field_name,
            "coverage": sig.coverage,
            "modification_level": sig.modification_level,
            "docmdp_ok": sig.docmdp_ok,
        }
        name = sig.field_name
        if sig.error:
            # A signature that cannot even be parsed is never treated as absent.
            return self.finding(
                "TL-SIG-003",
                f"Signature '{name}' is present but {sig.error}, so its integrity cannot be "
                "confirmed.",
                confidence=0.6,
                severity=Severity.MEDIUM,
                evidence={**ev, "error": sig.error},
            )
        if sig.intact is False or sig.valid is False:
            what = (
                "the signed bytes were altered"
                if sig.intact is False
                else ("the signature value does not verify")
            )
            return self.finding(
                "TL-SIG-003",
                f"Signature '{name}' does not match the document: {what}.",
                confidence=0.95,
                evidence=ev,
            )
        unchanged = _page_content_unchanged_after(doc, sig)
        if sig.certification and sig.docmdp_ok is False:
            # A certification signature restricted the changes allowed afterwards.
            return self.finding(
                "TL-SIG-002",
                f"'{name}' is a certification signature, and the document was changed afterwards "
                "in a way the certifier did not allow"
                + (" (the page content itself is unchanged)." if unchanged else "."),
                confidence=0.85,
                severity=Severity.MEDIUM if unchanged else None,
                evidence={**ev, "page_content_unchanged": unchanged},
            )
        if sig.modification_level == "OTHER" or sig.coverage == "UNCLEAR":
            if unchanged and sig.coverage != "UNCLEAR":
                # pyHanko's default policy rejects, for example, a reviewer's sticky note added
                # after an approval signature. When every later revision keeps the signed page
                # content and fonts, the change is outside the visible page content; overlay
                # annotations on top of text are reported separately by TL-OVL-002.
                return self.finding(
                    "TL-SIG-004",
                    f"After '{name}' signed, changes were made outside the page content (for "
                    "example annotations or metadata). The strict signature validator does not "
                    "whitelist them, but no page text or font changed.",
                    confidence=0.7,
                    evidence={**ev, "page_content_unchanged": True},
                )
            return self.finding(
                "TL-SIG-002",
                f"The document was changed after '{name}' signed it, and the changes are not of "
                "a kind a signature permits (form filling, annotations or validation data).",
                confidence=0.9,
                evidence=ev,
            )
        if sig.modification_level in PERMITTED_LEVELS:
            return self.finding(
                "TL-SIG-004",
                f"After '{name}' signed, only permitted changes were made "
                f"({_LEVEL_TEXT[sig.modification_level]}).",
                confidence=0.85,
                evidence=ev,
            )
        if sig.intact and sig.valid:
            return self.finding(
                "TL-SIG-001",
                f"Signature '{name}' is intact and nothing was changed after signing. (The "
                "signer's identity was not checked against a trust list.)",
                confidence=0.9,
                evidence=ev,
            )
        return None


def signed_revision_index(doc: Document, sig: SignatureInfo) -> int | None:
    """Index of the revision a signature covers (the first one ending at or after its range)."""
    if not sig.signed_revision_end:
        return None
    rev = next((r for r in doc.revisions if r.end_offset >= sig.signed_revision_end), None)
    return rev.index if rev else None


def _page_content_unchanged_after(doc: Document, sig: SignatureInfo) -> bool:
    """True when every revision after the signed one keeps page content and fonts identical."""
    index = signed_revision_index(doc, sig)
    if index is None:
        return False
    signed = doc.revisions[index - 1]
    later = [r for r in doc.revisions if r.index > index]
    return (
        signed.readable
        and bool(later)
        and all(
            r.readable
            and r.page_content_hashes == signed.page_content_hashes
            and r.page_fonts == signed.page_fonts
            for r in later
        )
    )

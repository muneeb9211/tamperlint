"""TL-META and TL-ID: metadata and document-identifier consistency."""

from __future__ import annotations

import re
from datetime import timedelta
from itertools import pairwise

from tamperlint.detectors.base import Detector
from tamperlint.document import Document
from tamperlint.models import Finding, Layer, Severity
from tamperlint.util import parse_pdf_date

# General-purpose PDF editors and online PDF services. Matching is case-insensitive.
# Office suites, browsers and report generators are deliberately NOT listed: they create
# genuine documents every day.
EDITOR_PATTERNS: dict[str, str] = {
    r"ilovepdf": "iLovePDF",
    r"smallpdf": "Smallpdf",
    r"\bpdf24\b(?!\s*creator)": "PDF24",  # "PDF24 Creator" is a print-to-PDF driver
    r"pdfescape": "PDFescape",
    r"\bsejda\b": "Sejda",
    r"pdf-?xchange\s+editor": "PDF-XChange Editor",
    r"foxit\s+(?:phantompdf|pdf\s+editor)": "Foxit PDF Editor",
    r"nitro\s*pro": "Nitro Pro",
    r"pdffiller": "pdfFiller",
    r"soda\s*pdf": "Soda PDF",
    r"pdfelement|wondershare": "PDFelement",
    r"\bcanva\b": "Canva",
    r"pdfcandy": "PDF Candy",
    r"docfly": "DocFly",
}
_COMPILED = [(re.compile(p, re.IGNORECASE), label) for p, label in EDITOR_PATTERNS.items()]
_DATE_TOLERANCE = timedelta(days=1)


def match_editor(text: str | None) -> str | None:
    if not text:
        return None
    for pattern, label in _COMPILED:
        if pattern.search(text):
            return label
    return None


class MetadataDetector(Detector):
    name = "metadata"
    layer = Layer.STRUCTURE
    rule_ids = ("TL-META-001", "TL-META-002", "TL-META-003", "TL-META-004", "TL-ID-001")

    def applies(self, doc: Document) -> str | None:
        return None if doc.kind == "pdf" else "not a PDF"

    def run(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        findings += self._editors(doc)
        findings += self._dates(doc)
        findings += self._revision_changes(doc)
        return findings

    def _editors(self, doc: Document) -> list[Finding]:
        info, xmp = doc.metadata.info, doc.metadata.xmp
        sources = {
            "Producer": info.get("Producer"),
            "Creator": info.get("Creator"),
            "XMP CreatorTool": xmp.get("xmp:CreatorTool"),
            "XMP Producer": xmp.get("pdf:Producer"),
        }
        for i, agent in enumerate(doc.metadata.xmp_history_agents, start=1):
            sources[f"XMP history entry {i}"] = agent
        for rev in doc.revisions:
            sources[f"Revision {rev.index} Producer"] = rev.producer
        hits = {field: label for field, value in sources.items() if (label := match_editor(value))}
        if not hits:
            return []
        tools = sorted(set(hits.values()))
        # Banks and billing systems do not issue statements or invoices from these tools, but
        # people use them every day to merge, compress or design other documents.
        financial = doc.doc_type in ("statement", "invoice")
        return [
            self.finding(
                "TL-META-001",
                f"Metadata shows the file passed through {', '.join(tools)}, a general-purpose "
                "PDF editor or online PDF service. "
                + (
                    "Institutions rarely issue statements or invoices from these tools."
                    if financial
                    else "That is common for other documents, so it is only a hint here."
                ),
                confidence=0.6,
                severity=None if financial else Severity.LOW,
                evidence={"fields": hits},
            )
        ]

    def _dates(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        info, xmp = doc.metadata.info, doc.metadata.xmp
        pairs = (
            ("creation", info.get("CreationDate"), xmp.get("xmp:CreateDate")),
            ("modification", info.get("ModDate"), xmp.get("xmp:ModifyDate")),
        )
        for label, info_value, xmp_value in pairs:
            a, b = parse_pdf_date(info_value), parse_pdf_date(xmp_value)
            if a and b and abs(a - b) > _DATE_TOLERANCE:
                findings.append(
                    self.finding(
                        "TL-META-002",
                        f"The {label} date is {info_value} in the document information but "
                        f"{xmp_value} in the XMP metadata.",
                        confidence=0.5,
                        evidence={"info": info_value, "xmp": xmp_value},
                    )
                )
        created = parse_pdf_date(info.get("CreationDate")) or parse_pdf_date(
            xmp.get("xmp:CreateDate")
        )
        modified = parse_pdf_date(info.get("ModDate")) or parse_pdf_date(xmp.get("xmp:ModifyDate"))
        # Tools often label local time with the wrong time zone, which puts the modification a
        # few hours "before" the creation. Only a reversal that holds on both clocks counts.
        if (
            created
            and modified
            and modified < created - timedelta(minutes=1)
            and modified.replace(tzinfo=None) < created.replace(tzinfo=None) - timedelta(minutes=1)
        ):
            findings.append(
                self.finding(
                    "TL-META-003",
                    f"The file claims it was last modified ({modified.isoformat()}) before it "
                    f"was created ({created.isoformat()}).",
                    confidence=0.6,
                    evidence={"created": created.isoformat(), "modified": modified.isoformat()},
                )
            )
        return findings

    def _revision_changes(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        readable = [r for r in doc.revisions if r.readable]
        for prev, cur in pairwise(readable):
            if prev.producer and cur.producer and prev.producer != cur.producer:
                findings.append(
                    self.finding(
                        "TL-META-004",
                        f"Revision {prev.index} was written by '{prev.producer}' and revision "
                        f"{cur.index} by '{cur.producer}'.",
                        confidence=0.5,
                        revision=cur.index,
                        evidence={"before": prev.producer, "after": cur.producer},
                    )
                )
            if prev.permanent_id and cur.permanent_id and prev.permanent_id != cur.permanent_id:
                findings.append(
                    self.finding(
                        "TL-ID-001",
                        f"The permanent document ID changed between revision {prev.index} and "
                        f"revision {cur.index}; the file was re-identified during editing.",
                        confidence=0.55,
                        revision=cur.index,
                        evidence={"before": prev.permanent_id, "after": cur.permanent_id},
                    )
                )
        return findings

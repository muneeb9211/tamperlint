"""Public Python API.

>>> from tamperlint import check
>>> report = check("statement.pdf")
>>> report.verdict
<Verdict.INTACT: 'INTACT'>
"""

from __future__ import annotations

import logging
from pathlib import Path

from tamperlint.detectors.base import Detector
from tamperlint.detectors.registry import load_detectors
from tamperlint.document import Document
from tamperlint.fusion import FusionResult, fuse
from tamperlint.loaders import load_bytes, load_path
from tamperlint.logic.doctype import DOC_TYPES, detect_doc_type
from tamperlint.models import DetectorRun, FileInfo, Finding, Report, Verdict

log = logging.getLogger(__name__)

BASE_LIMITATIONS = [
    "Findings are evidence for a human reviewer, not proof of fraud.",
    "A document rebuilt from scratch in the issuer's own software leaves few structural traces.",
    "Edits made before the PDF was generated (for example in a spreadsheet) cannot be detected "
    "from the file structure.",
]


def check(
    source: str | Path | bytes,
    *,
    name: str | None = None,
    doc_type: str = "auto",
    detectors: list[Detector] | None = None,
) -> Report:
    """Analyse a PDF or image and return a :class:`~tamperlint.models.Report`."""
    doc = load_bytes(source, name or "document") if isinstance(source, bytes) else load_path(source)
    return analyze_document(doc, doc_type=doc_type, detectors=detectors)


def analyze_document(
    doc: Document, *, doc_type: str = "auto", detectors: list[Detector] | None = None
) -> Report:
    if doc_type != "auto" and doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be 'auto' or one of {', '.join(DOC_TYPES)}")
    doc.doc_type = detect_doc_type(doc) if doc_type == "auto" else doc_type
    runs: list[DetectorRun] = []
    findings: list[Finding] = []
    for det in detectors if detectors is not None else load_detectors():
        reason = det.applies(doc)
        if reason is not None:
            runs.append(
                DetectorRun(name=det.name, layer=det.layer, status="skipped", reason=reason)
            )
            continue
        try:
            found = det.run(doc)
        except Exception as exc:
            # Details go to the log; the report only says which check failed and why broadly.
            log.exception("detector %s failed on %s", det.name, doc.name)
            runs.append(
                DetectorRun(
                    name=det.name,
                    layer=det.layer,
                    status="error",
                    reason=f"internal error ({type(exc).__name__}); see the log for details",
                )
            )
            continue
        findings.extend(found)
        runs.append(DetectorRun(name=det.name, layer=det.layer, status="ran", findings=len(found)))

    findings.sort(key=lambda f: (-f.severity.rank, -f.confidence, f.page or 0, f.rule_id))
    result = fuse(findings, runs)
    if not doc.pages:
        result = FusionResult(
            Verdict.INCONCLUSIVE, result.score, "The document has no pages to analyse."
        )
    limitations = list(BASE_LIMITATIONS)
    limitations += [w for w in doc.load_warnings if w not in limitations]
    if doc.kind == "image":
        limitations.append(
            "Image input: only pixel-level checks apply; structure checks need a PDF."
        )
    return Report(
        file=FileInfo(
            name=doc.name,
            sha256=doc.sha256,
            size_bytes=len(doc.data),
            kind=doc.kind,
            pages=doc.page_count or len(doc.pages),
        ),
        doc_type=doc.doc_type,
        verdict=result.verdict,
        score=result.score,
        summary=result.summary,
        findings=findings,
        detectors=runs,
        limitations=limitations,
    )

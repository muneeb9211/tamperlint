"""Detector base class.

A detector inspects a :class:`~tamperlint.document.Document` and returns findings for the rules
it owns. Detectors must be side-effect free and must never raise for malformed input: they
should return an empty list or explain why they do not apply.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from tamperlint.document import Document
from tamperlint.models import BBox, Finding, Layer, Severity
from tamperlint.rules import get_rule


class Detector(ABC):
    name: ClassVar[str]
    layer: ClassVar[Layer]
    rule_ids: ClassVar[tuple[str, ...]]

    def applies(self, doc: Document) -> str | None:
        """Return ``None`` when the detector can run, otherwise a reason for skipping it."""
        return None

    @abstractmethod
    def run(self, doc: Document) -> list[Finding]:
        """Analyse the document and return findings."""

    def finding(
        self,
        rule_id: str,
        message: str,
        *,
        confidence: float,
        severity: Severity | None = None,
        page: int | None = None,
        bbox: BBox | None = None,
        revision: int | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> Finding:
        if rule_id not in self.rule_ids:
            raise ValueError(f"{self.name} does not own rule {rule_id}")
        rule = get_rule(rule_id)
        return Finding(
            rule_id=rule.id,
            title=rule.title,
            layer=rule.layer,
            severity=severity or rule.severity,
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            message=message,
            page=page,
            bbox=bbox,
            revision=revision,
            evidence=evidence or {},
        )

"""Public data models: findings, detector runs and the final report."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tamperlint.version import __version__

REPORT_SCHEMA_VERSION = "1.0"


class Severity(StrEnum):
    """How much a finding contributes to the verdict."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}


class Layer(StrEnum):
    """The kind of evidence a detector looks at."""

    STRUCTURE = "structure"  # revisions, signatures, metadata, IDs
    CONTENT = "content"  # fonts, content streams, overlays
    GEOMETRY = "geometry"  # text-line alignment and sizing
    IMAGE = "image"  # pixel-level forensics
    LOGIC = "logic"  # arithmetic and date consistency of the content


class Verdict(StrEnum):
    INTACT = "INTACT"
    SUSPICIOUS = "SUSPICIOUS"
    INCONCLUSIVE = "INCONCLUSIVE"


class BBox(BaseModel):
    """A rectangle in PDF points, origin at the top-left of the page."""

    model_config = ConfigDict(frozen=True)

    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top


class Finding(BaseModel):
    """One piece of evidence produced by a detector."""

    rule_id: str = Field(description="Stable rule identifier, e.g. TL-FONT-002")
    title: str
    layer: Layer
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0, description="Detector confidence in this finding")
    message: str = Field(description="Plain-language explanation for a human reviewer")
    page: int | None = Field(default=None, ge=1, description="1-based page number")
    bbox: BBox | None = None
    revision: int | None = Field(default=None, ge=1, description="1-based PDF revision")
    evidence: dict[str, Any] = Field(default_factory=dict)


class DetectorRun(BaseModel):
    name: str
    layer: Layer
    status: Literal["ran", "skipped", "error"]
    reason: str | None = None
    findings: int = 0


class FileInfo(BaseModel):
    name: str
    sha256: str
    size_bytes: int
    kind: Literal["pdf", "image"]
    pages: int


class ToolInfo(BaseModel):
    name: str = "tamperlint"
    version: str = __version__


class Report(BaseModel):
    """The complete result of analysing one file."""

    schema_version: str = REPORT_SCHEMA_VERSION
    tool: ToolInfo = Field(default_factory=ToolInfo)
    file: FileInfo
    doc_type: str = "generic"
    verdict: Verdict
    score: float = Field(ge=0.0, le=1.0, description="Evidence strength, not a probability")
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    detectors: list[DetectorRun] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def findings_at_least(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity.rank >= severity.rank]

"""Discover built-in and third-party detectors."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from tamperlint.detectors.base import Detector
from tamperlint.detectors.content.edits import EditMarkerDetector
from tamperlint.detectors.content.fonts import FontDetector
from tamperlint.detectors.content.overlays import OverlayDetector
from tamperlint.detectors.geometry.alignment import AlignmentDetector
from tamperlint.detectors.image.copy_move import CopyMoveDetector
from tamperlint.detectors.image.jpeg_grid import JpegGridDetector
from tamperlint.detectors.logic.identifiers import IdentifierDetector
from tamperlint.detectors.logic.invoice import InvoiceDetector
from tamperlint.detectors.logic.statement import StatementDetector
from tamperlint.detectors.structure.metadata import MetadataDetector
from tamperlint.detectors.structure.revisions import RevisionDetector
from tamperlint.detectors.structure.signatures import SignatureDetector

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "tamperlint.detectors"

BUILTIN_DETECTORS: tuple[type[Detector], ...] = (
    SignatureDetector,
    RevisionDetector,
    MetadataDetector,
    EditMarkerDetector,
    FontDetector,
    OverlayDetector,
    AlignmentDetector,
    StatementDetector,
    InvoiceDetector,
    IdentifierDetector,
    JpegGridDetector,
    CopyMoveDetector,
)


def load_detectors(include_plugins: bool = True) -> list[Detector]:
    detectors: list[Detector] = [cls() for cls in BUILTIN_DETECTORS]
    if include_plugins:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                cls = ep.load()
                if isinstance(cls, type) and issubclass(cls, Detector):
                    detectors.append(cls())
                else:
                    log.warning("Entry point %s is not a Detector subclass; ignored", ep.name)
            except Exception:
                log.exception("Failed to load detector plugin %s", ep.name)
    return detectors

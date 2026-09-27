"""TL-IMG-001: local inconsistencies in the JPEG 8x8 block grid.

JPEG compresses an image in 8x8 blocks, which leaves faint discontinuities every 8 pixels. When
content from another image is pasted in, its grid is usually shifted relative to the host image,
or missing altogether. We estimate, for overlapping windows, how strong the grid is and at which
phase (offset 0-7) it sits, then flag textured regions whose phase disagrees with the rest of the
image, or whose grid is missing while all their neighbours have one.

Limitations (reported, not hidden): if the edited image was re-saved as JPEG at ordinary quality,
the new compression lays a fresh aligned grid over everything and local traces fade; flat white
paper carries almost no grid signal, so only textured areas (text, stamps, photos) are assessed.
"""

from __future__ import annotations

import cv2
import numpy as np

from tamperlint.detectors.base import Detector
from tamperlint.document import Document, Raster
from tamperlint.models import Finding, Layer, Severity

WINDOW = 96
STEP = 48
SMOOTH = 10.0  # neighbouring differences above this are edges (text strokes), not blocking
MIN_SAMPLES = 400  # smooth pixel pairs a window needs before its grid can be judged
STRONG = 1.6  # grid strength: energy at the peak phase / median energy over the 8 phases
ABSENT = 1.15
WEAK_RATIO = 0.6  # a window at under 60% of its neighbours' grid strength counts as weakened
MIN_GLOBAL_SHARE = 0.5  # share of assessable windows that must show the grid for the check to apply
MIN_EXCESS = 0.12  # grey levels of blocking energy above the median phase (scans measure 0.2-1.0)
MIN_TEXTURE = 6.0  # grey-level standard deviation of a window with real content


def _excess(g: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """Blocking evidence between neighbours along ``axis``.

    For each pixel pair the step |I(x+1) - I(x)| is compared with the mean of the neighbouring
    steps. At a JPEG block boundary the step is systematically larger. Pairs next to strong
    edges are masked out, so text strokes do not masquerade as blocking.
    """
    d = np.abs(np.diff(g, axis=axis))
    if axis == 1:
        mid, left, right = d[:, 1:-1], d[:, :-2], d[:, 2:]
    else:
        mid, left, right = d[1:-1, :], d[:-2, :], d[2:, :]
    smooth = (mid < SMOOTH) & (left < SMOOTH) & (right < SMOOTH)
    excess = np.where(smooth, np.maximum(mid - 0.5 * (left + right), 0.0), 0.0)
    return excess.astype(np.float32), smooth


def grid_windows(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return per-window (strength, phase, assessable) maps."""
    g = gray.astype(np.float32)
    ex_h, ok_h = _excess(g, 1)  # column boundary between x+1 and x+2 -> phase (x + 2) % 8
    ex_v, ok_v = _excess(g, 0)
    h, w = g.shape
    rows = max(1, (h - WINDOW) // STEP + 1)
    cols = max(1, (w - WINDOW) // STEP + 1)
    strength = np.zeros((rows, cols), np.float32)
    phase = np.full((rows, cols), -1, np.int8)
    assessable = np.zeros((rows, cols), bool)
    phase_of = (np.arange(max(h, w)) + 2) % 8
    for r in range(rows):
        y = r * STEP
        for c in range(cols):
            x = c * STEP
            eh = ex_h[y : y + WINDOW, x : x + WINDOW - 3]
            nh = ok_h[y : y + WINDOW, x : x + WINDOW - 3]
            ev = ex_v[y : y + WINDOW - 3, x : x + WINDOW]
            nv = ok_v[y : y + WINDOW - 3, x : x + WINDOW]
            if nh.sum() + nv.sum() < MIN_SAMPLES:
                continue
            ph_cols, ph_rows = phase_of[x : x + WINDOW - 3], phase_of[y : y + WINDOW - 3]
            energy = np.bincount(ph_cols, eh.sum(axis=0), minlength=8) + np.bincount(
                ph_rows, ev.sum(axis=1), minlength=8
            )
            count = np.bincount(ph_cols, nh.sum(axis=0), minlength=8) + np.bincount(
                ph_rows, nv.sum(axis=1), minlength=8
            )
            mean = energy / np.maximum(count, 1)
            med = float(np.median(mean))
            if med <= 1e-6:
                continue
            # Smooth, noise-free areas (gradients, vignetting) only show quantisation steps,
            # which mimic off-grid blocking. Judge a window only if it carries real blocking
            # energy or real texture (text, stamps, paper grain).
            if (
                float(mean.max()) - med < MIN_EXCESS
                and g[y : y + WINDOW, x : x + WINDOW].std() < MIN_TEXTURE
            ):
                continue
            assessable[r, c] = True
            strength[r, c] = float(mean.max()) / med
            phase[r, c] = int(mean.argmax())
    return strength, phase, assessable


class JpegGridDetector(Detector):
    name = "jpeg-grid"
    layer = Layer.IMAGE
    rule_ids = ("TL-IMG-001",)

    def applies(self, doc: Document) -> str | None:
        return None if doc.rasters else "no raster images to analyse"

    def run(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        for raster in doc.rasters:
            findings += self._analyse(raster, doc)
        return findings

    def _analyse(self, raster: Raster, doc: Document) -> list[Finding]:
        if min(raster.gray.shape[:2]) < 2 * WINDOW:
            doc.load_warnings.append(f"Page {raster.page}: image too small for JPEG grid analysis.")
            return []
        embedded = raster.source == "embedded"
        strength, phase, textured = grid_windows(raster.gray)  # textured = assessable
        n_textured = int(textured.sum())
        strong = textured & (strength >= STRONG)
        if n_textured < 20 or strong.sum() < MIN_GLOBAL_SHARE * n_textured:
            doc.load_warnings.append(
                f"Page {raster.page}: no consistent JPEG grid found, so grid analysis was not "
                "conclusive for this image."
            )
            return []
        global_phase = int(np.bincount(phase[strong].astype(np.int64), minlength=8).argmax())
        aligned = strong & (phase == global_phase)
        misaligned = strong & (phase != global_phase)
        # "Missing or weakened grid": a window whose grid is much weaker than that of its aligned
        # neighbours (pasted content carries its own, differently placed grid, which the
        # final compression only partly overwrites).
        kernel = np.ones((3, 3), np.float32)
        n_aligned = cv2.filter2D(
            aligned.astype(np.float32), -1, kernel, borderType=cv2.BORDER_CONSTANT
        )
        s_aligned = cv2.filter2D(
            (strength * aligned).astype(np.float32), -1, kernel, borderType=cv2.BORDER_CONSTANT
        )
        neighbour_strength = s_aligned / np.maximum(n_aligned, 1.0)
        weak = (strength < ABSENT) | (strength < WEAK_RATIO * neighbour_strength)
        missing = textured & ~aligned & weak & (n_aligned >= 3)
        anomalous = (misaligned | missing).astype(np.uint8)

        count, labels, stats, _ = cv2.connectedComponentsWithStats(anomalous, connectivity=8)
        findings: list[Finding] = []
        for label in range(1, count):
            windows = int(stats[label, cv2.CC_STAT_AREA])
            if windows < 3:
                continue
            c0, r0 = int(stats[label, cv2.CC_STAT_LEFT]), int(stats[label, cv2.CC_STAT_TOP])
            cw, rh = int(stats[label, cv2.CC_STAT_WIDTH]), int(stats[label, cv2.CC_STAT_HEIGHT])
            region = labels == label
            share_misaligned = float(misaligned[region].mean())
            x0, y0 = c0 * STEP, r0 * STEP
            x1, y1 = (c0 + cw - 1) * STEP + WINDOW, (r0 + rh - 1) * STEP + WINDOW
            kind = "shifted relative to" if share_misaligned >= 0.5 else "missing, unlike"
            findings.append(
                self.finding(
                    "TL-IMG-001",
                    f"On page {raster.page}, the JPEG compression grid in a region of "
                    f"{x1 - x0}x{y1 - y0} px is {kind} the rest of the image, which happens when "
                    "content from another image is pasted in."
                    + (
                        " Inside a PDF this is weak evidence: scans are often compressed again, "
                        "in parts, when the PDF is made."
                        if embedded
                        else ""
                    ),
                    confidence=min(0.85, 0.45 + 0.05 * windows),
                    # Real scans embedded in PDFs show such regions without any editing, so
                    # there it is a hint for the reviewer, not evidence that sets the verdict.
                    severity=Severity.LOW if embedded else None,
                    page=raster.page,
                    bbox=raster.to_page(x0, y0, x1, y1),
                    evidence={
                        "windows": windows,
                        "misaligned_share": round(share_misaligned, 2),
                        "global_phase": global_phase,
                        "pixels": [x0, y0, x1, y1],
                    },
                )
            )
        return findings

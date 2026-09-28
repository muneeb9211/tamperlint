"""TL-IMG-002: duplicated regions (copy-move).

Documents repeat glyphs everywhere, so naive keypoint matching floods with false matches. Three
safeguards keep this check honest:

1. Matches are grouped by displacement vector; a copied region moves all its keypoints by the
   same vector.
2. A group must span a region taller than a single line of text.
3. Every candidate is verified pixel by pixel: the source region, shifted by the displacement,
   must reproduce the target region almost exactly. Coincidental glyph matches across different
   rows fail this test because the surrounding content differs.
"""

from __future__ import annotations

from collections import defaultdict

import cv2
import numpy as np

from tamperlint.detectors.base import Detector
from tamperlint.document import Document, Raster
from tamperlint.models import Finding, Layer, Severity
from tamperlint.util import overlap_ratio

MAX_DESCRIPTOR_DISTANCE = 24
MIN_SHIFT = 24  # px; ignore near-self matches
BIN = 3  # px displacement bins
MIN_POINTS = 14
MIN_SIMILARITY = 0.9
INK = 200  # grey level below which a pixel counts as ink
PAGE_SCALE_SHARE = 0.2  # a duplicated region this large (of the image) is a two-up form
SAME_ROW = 4  # px: a copy moved only sideways repeats a design element
STRIP = 6  # px: height of the horizontal strips used to find the copied band in a region
PERIODIC_SIMILARITY = 0.75  # matching this well at another offset means the content repeats
PERIODIC_MARGIN = 0.1


def _groups(points: np.ndarray, link: float) -> list[np.ndarray]:
    """Single-linkage grouping of points closer than ``link`` pixels.

    Points are hashed into ``link``-sized cells and only neighbouring cells are compared, so
    memory stays linear in the number of points.
    """
    n = len(points)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (x, y) in enumerate(points):
        cells[(int(x // link), int(y // link))].append(i)
    for (cx, cy), members in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                others = cells.get((cx + dx, cy + dy))
                if not others:
                    continue
                for i in members:
                    near = [
                        j for j in others if j > i and np.hypot(*(points[i] - points[j])) < link
                    ]
                    for j in near:
                        parent[find(i)] = find(j)
    buckets: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        buckets[find(i)].append(i)
    return [np.array(idx) for idx in buckets.values()]


def _similarity(gray: np.ndarray, box: tuple[int, int, int, int], shift: tuple[int, int]) -> float:
    x0, y0, x1, y1 = box
    dx, dy = shift
    h, w = gray.shape
    if min(x0 + dx, y0 + dy) < 0 or x1 + dx > w or y1 + dy > h:
        return 0.0
    src = gray[y0:y1, x0:x1].astype(np.int16)
    dst = gray[y0 + dy : y1 + dy, x0 + dx : x1 + dx].astype(np.int16)
    ink = (src < INK) | (dst < INK)
    if ink.sum() < 200:
        return 0.0
    return float((np.abs(src - dst)[ink] < 24).mean())


def _copied_band(
    gray: np.ndarray, box: tuple[int, int, int, int], shift: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """The tallest horizontal band of ``box`` that is really repeated at ``shift``.

    Keypoints that match by coincidence (dates, words repeated from row to row) can stretch a
    region over rows that were not copied, which dilutes its similarity below the threshold even
    though the copied rows inside it match exactly. Strips without ink (the gaps between text
    rows) neither break nor extend a band.
    """
    x0, y0, x1, y1 = box
    dx, dy = shift
    h, w = gray.shape
    if min(x0 + dx, y0 + dy) < 0 or x1 + dx > w or y1 + dy > h:
        return None
    src = gray[y0:y1, x0:x1].astype(np.int16)
    dst = gray[y0 + dy : y1 + dy, x0 + dx : x1 + dx].astype(np.int16)
    ink = (src < INK) | (dst < INK)
    same = (np.abs(src - dst) < 24) & ink
    best: tuple[int, int] | None = None
    start: int | None = None
    for top in range(0, y1 - y0, STRIP):
        n = int(ink[top : top + STRIP].sum())
        if n < 20:
            continue
        if int(same[top : top + STRIP].sum()) >= MIN_SIMILARITY * n:
            start = top if start is None else start
            end = min(top + STRIP, y1 - y0)
            if best is None or end - start > best[1] - best[0]:
                best = (start, end)
        else:
            start = None
    return None if best is None else (x0, y0 + best[0], x1, y0 + best[1])


def _sheet_duplicate(gray: np.ndarray, shift: tuple[int, int]) -> bool:
    """True when the whole sheet repeats at this shift (two copies of a form side by side)."""
    h, w = gray.shape
    dx, dy = shift
    if abs(dx) < 0.3 * w and abs(dy) < 0.3 * h:
        return False
    x0, y0 = max(0, -dx), max(0, -dy)
    x1, y1 = min(w, w - dx), min(h, h - dy)
    if (x1 - x0) * (y1 - y0) < 0.25 * w * h:
        return False
    return _similarity(gray, (x0, y0, x1, y1), (dx, dy)) >= MIN_SIMILARITY


def _periodic(
    gray: np.ndarray, box: tuple[int, int, int, int], shift: tuple[int, int], score: float
) -> bool:
    """True when the region also matches at nearby offsets, i.e. the content simply repeats.

    A column of dates in a statement matches itself one row further down almost as well as five
    rows further down (about 0.84 against 0.92: the day digits differ, and a row pitch that is
    not a whole number of pixels blurs the neighbouring match). A genuinely copied block matches
    only at the displacement it was moved by; elsewhere it scores around 0.2-0.5.
    """
    dx, dy = shift
    threshold = min(PERIODIC_SIMILARITY, score - PERIODIC_MARGIN)
    for k in range(-80, 81):
        if abs(k) < 8:
            continue
        for alt in ((dx, dy + k), (dx + k, dy)):
            if _similarity(gray, box, alt) >= threshold:
                return True
    return False


def _regions(points: np.ndarray, link: float) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of keypoint groups; groups on the same lines are merged, because column
    gaps split a copied band of text into several groups."""
    boxes: list[list[int]] = []
    for idx in _groups(points, link):
        if len(idx) < MIN_POINTS // 2:
            continue
        g = points[idx]
        x0, y0 = (g.min(axis=0) - 8).astype(int)
        x1, y1 = (g.max(axis=0) + 8).astype(int)
        boxes.append([max(0, int(x0)), max(0, int(y0)), int(x1), int(y1), len(idx)])
    merged: list[list[int]] = []
    for b in sorted(boxes, key=lambda b: b[1]):
        for m in merged:
            overlap = min(m[3], b[3]) - max(m[1], b[1])
            if overlap > 0.5 * min(m[3] - m[1], b[3] - b[1]):
                m[0], m[1], m[2], m[3] = (
                    min(m[0], b[0]),
                    min(m[1], b[1]),
                    max(m[2], b[2]),
                    max(m[3], b[3]),
                )
                m[4] += b[4]
                break
        else:
            merged.append(b)
    return [(b[0], b[1], b[2], b[3]) for b in merged if b[4] >= MIN_POINTS]


class CopyMoveDetector(Detector):
    name = "copy-move"
    layer = Layer.IMAGE
    rule_ids = ("TL-IMG-002",)

    def applies(self, doc: Document) -> str | None:
        return None if doc.rasters else "no raster images to analyse"

    def run(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        for raster in doc.rasters:
            findings += self._analyse(raster)
        return findings

    def _analyse(self, raster: Raster) -> list[Finding]:
        gray = raster.gray
        orb = cv2.ORB_create(nfeatures=6000, fastThreshold=12)  # type: ignore[attr-defined]
        keypoints, desc = orb.detectAndCompute(gray, None)
        if desc is None or len(keypoints) < 2 * MIN_POINTS:
            return []
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        by_shift: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
        for candidates in matcher.knnMatch(desc, desc, k=4):
            for m in candidates:
                if m.queryIdx == m.trainIdx or m.distance > MAX_DESCRIPTOR_DISTANCE:
                    continue
                (x1, y1), (x2, y2) = keypoints[m.queryIdx].pt, keypoints[m.trainIdx].pt
                dx, dy = x2 - x1, y2 - y1
                if dx < 0 or (dx == 0 and dy < 0):
                    continue  # count each unordered pair once
                if dx * dx + dy * dy < MIN_SHIFT * MIN_SHIFT:
                    continue
                by_shift[(round(dx / BIN), round(dy / BIN))].append((x1, y1))

        h_img, w_img = gray.shape
        min_height = max(80, int(0.07 * min(h_img, w_img)))  # about three text lines
        min_width = int(0.3 * w_img)  # a copied band of text spans several columns
        link = max(36.0, 0.045 * min(h_img, w_img))  # joins keypoints on neighbouring lines
        seen: list[tuple[int, int, int, int]] = []
        findings: list[Finding] = []
        for (bx, by), pts in sorted(by_shift.items(), key=lambda kv: -len(kv[1])):
            if len(pts) < MIN_POINTS:
                break
            for region in _regions(np.array(pts, dtype=np.float32), link):
                x0, y0, x1, y1 = region
                # Either a tall region (stamp, signature, block of lines) or a wide band that
                # spans several columns. Short, narrow matches are repeated words in a column.
                tall = y1 - y0 >= min_height and x1 - x0 >= min_height
                wide = x1 - x0 >= min_width and y1 - y0 >= 0.5 * min_height
                if not (tall or wide):
                    continue
                shift = (bx * BIN, by * BIN)
                best = max(
                    (_similarity(gray, (x0, y0, x1, y1), (shift[0] + ex, shift[1] + ey)), ex, ey)
                    for ex in (-1, 0, 1)
                    for ey in (-1, 0, 1)
                )
                if best[0] < MIN_SIMILARITY:
                    # coincidental matches may have stretched the region: keep the copied band
                    band = _copied_band(
                        gray, (x0, y0, x1, y1), (shift[0] + best[1], shift[1] + best[2])
                    )
                    if band is None or not (
                        (band[3] - band[1] >= min_height and band[2] - band[0] >= min_height)
                        or (
                            band[2] - band[0] >= min_width and band[3] - band[1] >= 0.5 * min_height
                        )
                    ):
                        continue
                    x0, y0, x1, y1 = band
                    score = _similarity(gray, band, (shift[0] + best[1], shift[1] + best[2]))
                    if score < MIN_SIMILARITY:
                        continue
                    best = (score, best[1], best[2])
                dx, dy = shift[0] + best[1], shift[1] + best[2]
                if _periodic(gray, (x0, y0, x1, y1), (dx, dy), best[0]):
                    continue  # repeating layout (table columns, forms), not a copy
                box = (x0, y0, x1, y1)
                target = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                if any(
                    overlap_ratio(box, prev) > 0.3
                    or overlap_ratio(target, prev) > 0.3
                    or overlap_ratio(prev, box) > 0.3
                    or overlap_ratio(prev, target) > 0.3
                    for prev in seen
                ):
                    continue  # the same copy seen through another keypoint group
                seen += [box, target]
                # A copy covering a large part of the sheet is usually a form printed twice
                # (bank copy / customer copy), not an edit.
                page_scale = (x1 - x0) * (
                    y1 - y0
                ) >= PAGE_SCALE_SHARE * h_img * w_img or _sheet_duplicate(gray, (dx, dy))
                if page_scale:
                    severity: Severity | None = Severity.LOW
                    why = (
                        "The copy covers a large part of the sheet, which is normal for forms "
                        "printed twice (for example a bank copy and a customer copy)."
                    )
                elif abs(dy) <= SAME_ROW:
                    severity = Severity.LOW
                    why = (
                        "The copy sits beside the original on the same line, as repeated logos, "
                        "boxes and headings do in forms and letterheads."
                    )
                else:
                    severity = Severity.HIGH if best[0] >= 0.97 else None
                    why = "Copy-pasted content inside the same image looks like this."
                findings.append(
                    self.finding(
                        "TL-IMG-002",
                        f"On page {raster.page}, a {x1 - x0}x{y1 - y0} px region is repeated "
                        f"{abs(dx)} px {'right' if dx >= 0 else 'left'} and {abs(dy)} px "
                        f"{'down' if dy >= 0 else 'up'}, with {best[0]:.0%} of its ink pixels "
                        f"identical. {why}",
                        confidence=min(0.9, 0.5 + (best[0] - MIN_SIMILARITY) * 4),
                        severity=severity,
                        page=raster.page,
                        bbox=raster.to_page(x0 + dx, y0 + dy, x1 + dx, y1 + dy),
                        evidence={
                            "source_px": [int(x0), int(y0), int(x1), int(y1)],
                            "shift_px": [int(dx), int(dy)],
                            "similarity": round(best[0], 3),
                            "shift_bin": [int(bx), int(by)],
                        },
                    )
                )
        return findings

"""TL-GEO: text that does not line up with its row or column."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import pairwise
from statistics import median

from tamperlint.detectors.base import Detector
from tamperlint.document import Document, Page, Word, strip_subset_prefix
from tamperlint.models import Finding, Layer
from tamperlint.text import (
    CharIndex,
    font_family,
    group_rows,
    is_bold,
    parse_money,
    visible_chars,
    word_chars,
)

BASELINE_MIN_DEVIATION = 0.6  # points
BASELINE_MAX_DEVIATION = 2.0  # points: a retyped value is placed close to the line; wrapped
# or vertically centred cells sit half a line or more away
LEVEL_ROW = 0.3  # points: the other words of a table row share their baseline this closely
UNEVEN_PAGE = 5  # more deviating values than this, at varied offsets, is an uneven layout
SYSTEMATIC_COUNT = 3  # the same offset at the same x position this often is layout
COLUMN_TOLERANCE = 2.0  # points between right edges of values in the same column
_SUMMARY_LABEL = re.compile(
    r"\b(sub-?\s?total|total|tax|vat|gst|discount|opening|closing|balance b/?f|balance c/?f"
    r"|amount due|balance due|amount paid|net payable|payable|credit limit|available"
    r"|minimum payment|interest)\b",
    re.IGNORECASE,
)


def _is_value(text: str) -> bool:
    """An amount of at least three digits: what a forger retypes into a table."""
    return parse_money(text) is not None and sum(ch.isdigit() for ch in text) >= 3


def _split_blocks(column: list[Word]) -> list[list[Word]]:
    """Split right-aligned values into vertical blocks: two tables can share a margin."""
    ordered = sorted(column, key=lambda w: w.top)
    if len(ordered) < 2:
        return [ordered]
    gaps = [b.top - a.bottom for a, b in pairwise(ordered)]
    height = median(w.bottom - w.top for w in ordered)
    limit = max(3 * median(gaps), 2.5 * height)
    blocks: list[list[Word]] = [[ordered[0]]]
    for gap, w in zip(gaps, ordered[1:], strict=True):
        if gap > limit:
            blocks.append([])
        blocks[-1].append(w)
    return blocks


class AlignmentDetector(Detector):
    name = "alignment"
    layer = Layer.GEOMETRY
    rule_ids = ("TL-GEO-001", "TL-GEO-002")

    def applies(self, doc: Document) -> str | None:
        if doc.kind != "pdf":
            return "not a PDF"
        return None if any(p.words for p in doc.pages) else "no text layer"

    def run(self, doc: Document) -> list[Finding]:
        findings = self._baselines(doc)
        for page in doc.pages:
            findings += self._columns(page)
        return findings

    def _word_baseline(self, page: Page, word: Word, pool: CharIndex) -> float | None:
        chars = word_chars(page, word, pool)
        return median(c.baseline for c in chars) if chars else None

    def _baselines(self, doc: Document) -> list[Finding]:
        candidates = [(page, *c) for page in doc.pages for c in self._offsets(page)]
        # The same offset at the same position, on this page or others, is the template's
        # layout (for example vertically centred cells in a different size), not an edit.
        layout = Counter((round(w.x0 / 4), round(dev, 1)) for _, w, dev, _ in candidates)
        findings: list[Finding] = []
        for page, w, dev, size_ref in candidates:
            if layout[(round(w.x0 / 4), round(dev, 1))] >= SYSTEMATIC_COUNT:
                continue
            findings.append(
                self.finding(
                    "TL-GEO-001",
                    f"On page {page.number}, the value '{w.text}' sits {abs(dev):.1f} pt "
                    f"{'below' if dev > 0 else 'above'} the baseline shared by the other "
                    "words in its row.",
                    confidence=min(0.9, 0.5 + abs(dev) / size_ref),
                    page=page.number,
                    bbox=w.bbox,
                    evidence={"text": w.text, "deviation_pt": round(dev, 2)},
                )
            )
        return findings

    def _offsets(self, page: Page) -> list[tuple[Word, float, float]]:
        """Amounts off the baseline of their level table row: (word, offset, row font size)."""
        pool = CharIndex(visible_chars(page))
        candidates: list[tuple[Word, float, float]] = []
        for row in group_rows(page.words):
            if len(row) < 3 or not any(_is_value(w.text) for w in row):
                continue
            sizes = [w.size for w in row]
            size_ref = median(sizes)
            peers = [w for w in row if abs(w.size - size_ref) <= 0.15 * size_ref]
            if len(peers) < 3:
                continue
            baselines = {id(w): self._word_baseline(page, w, pool) for w in peers}
            # Only values are checked, and only in rows whose other words sit on one baseline
            # (a table row). Prose, superscripts and side-by-side text columns are uneven by
            # nature; a retyped amount in an otherwise level row is not.
            for w in peers:
                b = baselines[id(w)]
                if b is None or not _is_value(w.text):
                    continue
                others = [x for k, x in baselines.items() if k != id(w) and x is not None]
                if len(others) < 2:
                    continue
                level = [x for x in others if abs(x - median(others)) <= LEVEL_ROW]
                if len(level) < max(2, 0.6 * len(others)):
                    continue
                dev = b - median(level)
                if (
                    BASELINE_MIN_DEVIATION
                    <= abs(dev)
                    <= min(BASELINE_MAX_DEVIATION, 0.4 * size_ref)
                ):
                    candidates.append((w, dev, size_ref))
        # Many values off their rows by varied amounts is an uneven layout; a forger who
        # retypes several values tends to repeat the same small offset.
        if len(candidates) > UNEVEN_PAGE:
            offsets = Counter(round(dev * 4) for _, dev, _ in candidates)
            if offsets.most_common(1)[0][1] < 0.8 * len(candidates):
                return []
        return candidates

    def _columns(self, page: Page) -> list[Finding]:
        findings: list[Finding] = []
        # Totals and balances are often styled differently on purpose; leave them out.
        styled_rows = {
            id(w)
            for row in group_rows(page.words)
            if _SUMMARY_LABEL.search(" ".join(x.text for x in row))
            for w in row
        }
        values = [
            w for w in page.words if parse_money(w.text) is not None and id(w) not in styled_rows
        ]
        # Fonts the page uses for its own words (labels, descriptions), and for all its values.
        # A careful forger may retype most of a column (every later balance), so the font a
        # column mostly uses is not necessarily the original one.
        in_text = Counter(
            font_family(w.fontname) for w in page.words if parse_money(w.text) is None
        )
        in_values = Counter(font_family(w.fontname) for w in values)
        aligned: dict[int, list[Word]] = defaultdict(list)
        for w in sorted(values, key=lambda w: w.x1):
            key = next((k for k in aligned if abs(k / 10 - w.x1) <= COLUMN_TOLERANCE), None)
            aligned[key if key is not None else round(w.x1 * 10)].append(w)
        for column in (block for group in aligned.values() for block in _split_blocks(group)):
            if len(column) < 4:
                continue
            families = Counter(font_family(w.fontname) for w in column)
            family, family_n = families.most_common(1)[0]
            native = [f for f in families if in_text[f]]
            if (
                len(families) > 1
                and len(native) == 1
                and in_values[native[0]] > sum(in_values[f] for f in families if f != native[0])
            ):
                # One font is the page's own and sets most of its values: the others are foreign.
                family, family_n = native[0], families[native[0]]
            elif family_n < 0.6 * len(column):
                continue
            size_ref = median(w.size for w in column if font_family(w.fontname) == family)
            column_font = next(
                strip_subset_prefix(w.fontname) for w in column if font_family(w.fontname) == family
            )
            for w in column:
                fam = font_family(w.fontname)
                if fam != family:
                    own = strip_subset_prefix(w.fontname)
                    own = "an unnamed font" if own.lower() in ("", "unknown") else own
                    findings.append(
                        self.finding(
                            "TL-GEO-002",
                            f"On page {page.number}, the value '{w.text}' is set in {own} while "
                            f"{family_n} other values in its column use {column_font}"
                            + (", the font of the rest of the page." if in_text[family] else "."),
                            confidence=0.75,
                            page=page.number,
                            bbox=w.bbox,
                            evidence={"text": w.text, "font": w.fontname, "column_font": family},
                        )
                    )
                elif abs(w.size - size_ref) > 0.5 and not is_bold(w.fontname):
                    findings.append(
                        self.finding(
                            "TL-GEO-002",
                            f"On page {page.number}, the value '{w.text}' is {w.size:.1f} pt while "
                            f"the other values in its column are {size_ref:.1f} pt.",
                            confidence=0.55,
                            page=page.number,
                            bbox=w.bbox,
                            evidence={"text": w.text, "size": w.size, "column_size": size_ref},
                        )
                    )
        return findings

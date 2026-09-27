"""Assign the values of a table to its header columns, whatever the header alignment.

A header can sit over its values in three ways, and each needs a different rule:

* right-aligned headers end where the (right-aligned) numbers end, so a value belongs to the
  header whose right edge it reaches;
* left-aligned headers start where their column starts, so a value belongs to the last header
  that starts before the value ends. Tables printed from HTML, word processors and
  spreadsheets are often laid out like this, with the numbers still right-aligned;
* centred headers sit over the middle of their values.

The alignment is read from the table itself: the rightmost value of each row belongs to the
rightmost column (balance, line amount), and where those values end relative to that header
tells which rule applies.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from statistics import median
from typing import Literal

from tamperlint.document import Word

EDGE_TOLERANCE = 4.0  # points between a header edge and the values aligned to it
CENTRE_TOLERANCE = 6.0
MARGIN = 45.0  # points a centred column may extend beyond its outermost header
MIN_SAMPLES = 3

Alignment = Literal["left", "right", "centre"]


@dataclass(frozen=True)
class TableColumns:
    """Header spans ``name -> (x0, x1)`` and how the headers are aligned over their values."""

    spans: dict[str, tuple[float, float]]
    alignment: Alignment = "centre"

    def __bool__(self) -> bool:
        return bool(self.spans)

    def aligned_to(self, rightmost_values: list[Word]) -> TableColumns:
        """Infer the alignment from the rightmost value of each row of this table."""
        if not self.spans or len(rightmost_values) < MIN_SAMPLES:
            return self
        hx0, hx1 = max(self.spans.values(), key=lambda span: span[1])
        end = median(w.x1 for w in rightmost_values)
        middle = median((w.x0 + w.x1) / 2 for w in rightmost_values)
        alignment: Alignment
        if abs(hx1 - end) <= EDGE_TOLERANCE:
            alignment = "right"
        elif abs((hx0 + hx1) / 2 - middle) <= CENTRE_TOLERANCE:
            alignment = "centre"
        elif hx0 < end and hx1 < end - EDGE_TOLERANCE:
            alignment = "left"
        else:
            alignment = "centre"
        return replace(self, alignment=alignment)

    def column_of(self, w: Word) -> str | None:
        """The column a value belongs to, or None when it lies outside the table's columns."""
        ordered = sorted(self.spans.items(), key=lambda kv: kv[1][0])
        if self.alignment == "right":
            for i, (name, (x0, x1)) in enumerate(ordered):
                low = ordered[i - 1][1][1] if i else x0 - MARGIN
                if low < w.x1 <= x1 + EDGE_TOLERANCE:
                    return name
            return None
        if self.alignment == "left":
            for i, (name, (x0, _)) in enumerate(ordered):
                high = ordered[i + 1][1][0] if i + 1 < len(ordered) else math.inf
                if x0 < w.x1 < high:
                    return name
            return None
        centres = [(x0 + x1) / 2 for _, (x0, x1) in ordered]
        centre = (w.x0 + w.x1) / 2
        for i, (name, (x0, x1)) in enumerate(ordered):
            low = (centres[i - 1] + centres[i]) / 2 if i else x0 - MARGIN
            high = (centres[i] + centres[i + 1]) / 2 if i + 1 < len(ordered) else x1 + MARGIN
            if low <= centre < high:
                return name
        return None


def header_end(row: list[Word], last: int, stops: set[int]) -> float:
    """Right edge of the header cell whose name ends at ``row[last]``.

    Words that follow within a normal word gap belong to the same cell ("Price (incl. GST)",
    "Balance (PKR)"); the cell ends at a column gap or where another header starts.
    """
    end = row[last].x1
    for j in range(last + 1, len(row)):
        if j in stops or row[j].x0 - end > 0.6 * max(row[j].size, 1.0):
            break
        end = row[j].x1
    return end


def layouts(
    rows: list[list[Word]],
    header_of: Callable[[list[Word]], dict[str, tuple[float, float]]],
    rightmost_value: Callable[[list[Word]], Word | None],
) -> dict[int, TableColumns]:
    """Column layouts keyed by the index of each header row in ``rows``.

    ``header_of(row)`` returns header spans (empty when the row is not a header) and
    ``rightmost_value(row)`` the row's rightmost value word, or None. A table runs from its
    header to the next header, across pages.
    """
    starts = [(i, spans) for i, row in enumerate(rows) if (spans := header_of(row))]
    found: dict[int, TableColumns] = {}
    for n, (start, spans) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(rows)
        values = [v for row in rows[start + 1 : end] if (v := rightmost_value(row)) is not None]
        found[start] = TableColumns(spans).aligned_to(values)
    return found

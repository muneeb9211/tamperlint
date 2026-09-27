"""TL-FONT: fonts that betray retyped or pasted text."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import combinations

from tamperlint.detectors.base import Detector
from tamperlint.document import Char, Document, strip_subset_prefix, subset_prefix
from tamperlint.models import BBox, Finding, Layer, Severity
from tamperlint.text import font_family, is_bold, is_symbol_font, parse_money, visible_chars

_NUMERIC = set("0123456789.,-+()%")
MARGIN_BAND = 0.07  # top and bottom share of the page treated as header/footer
COUNTER_TAG_DISTANCE = 2  # subset tags this close were numbered by one producer in one export
EDIT_SUBSET_CHARS = (3, 40)  # size of a subset added to retype a value
_ITALIC = re.compile(r"italic|oblique", re.IGNORECASE)


def _describe(font: str) -> str:
    base = strip_subset_prefix(font)
    return "an unnamed font" if base.lower() in ("", "unknown") else f"the font {base}"


def _numbered_by_one_producer(tags: list[str]) -> bool:
    """Tags such as BAAAAA/CAAAAA (LibreOffice) or BCDEEE/BCDIEE (Microsoft Office) come from a
    counter, and those producers may give one font several subsets in a single export.
    Editors and most other producers pick random tags, which differ almost everywhere."""
    return all(
        sum(x != y for x, y in zip(a, b, strict=False)) <= COUNTER_TAG_DISTANCE
        for a, b in combinations(tags, 2)
    )


def _styled(font: str) -> bool:
    return is_bold(font) or bool(_ITALIC.search(strip_subset_prefix(font)))


def _looks_retyped(chars: list[Char]) -> bool:
    """A handful of characters, mostly digits: what an editor embeds to retype an amount."""
    low, high = EDIT_SUBSET_CHARS
    numeric = sum(c.text in _NUMERIC for c in chars)
    return low <= len(chars) <= high and numeric >= 0.6 * len(chars)


class FontDetector(Detector):
    name = "fonts"
    layer = Layer.CONTENT
    rule_ids = ("TL-FONT-001", "TL-FONT-002")

    def applies(self, doc: Document) -> str | None:
        if doc.kind != "pdf":
            return "not a PDF"
        return None if any(p.chars for p in doc.pages) else "no text layer"

    def run(self, doc: Document) -> list[Finding]:
        return self._subsets(doc) + self._isolated_fonts(doc)

    def _subsets(self, doc: Document) -> list[Finding]:
        """Two subsets of one font with the same characters on one page.

        Generators split large character sets across subsets (for example 256 glyphs each),
        so different subsets normally hold different characters; the same character in two
        subsets means text was set in separate sessions. Only subsets that meet on a page
        count, since a PDF assembled from several files keeps each file's subsets on its own
        pages. Design and print workflows also repeat subsets when they place artwork, so the
        finding is weak evidence unless the extra subset holds a few digits: a retyped amount.
        """
        findings: list[Finding] = []
        reported: set[str] = set()
        for page in doc.pages:
            # font base name -> subset tag -> characters drawn with that subset
            members: dict[str, dict[str, list[Char]]] = defaultdict(lambda: defaultdict(list))
            for ch in visible_chars(page):
                tag = subset_prefix(ch.fontname)
                if tag and ch.text.strip():
                    members[strip_subset_prefix(ch.fontname)][tag].append(ch)
            for base, subsets in sorted(members.items()):
                tags = sorted(subsets)
                if base in reported or len(tags) < 2 or _numbered_by_one_producer(tags):
                    continue
                glyphs = [{c.text for c in chars} for chars in subsets.values()]
                shared = sorted({c for a, b in combinations(glyphs, 2) for c in a & b})
                if not shared:
                    continue
                reported.add(base)
                smallest = min(subsets.values(), key=len)
                retyped = _looks_retyped(smallest)
                findings.append(
                    self.finding(
                        "TL-FONT-001",
                        f"On page {page.number}, the font {base} is embedded as {len(tags)} "
                        f"separate subsets ({', '.join(tags)}) that both contain the characters "
                        f"{' '.join(shared[:12])}. "
                        + (
                            f"The smaller subset holds only {len(smallest)} characters, mostly "
                            "digits, as when an editor embeds the font again to retype a value."
                            if retyped
                            else "Text set in one session shares one subset; design and print "
                            "workflows also repeat subsets when they place artwork."
                        ),
                        confidence=0.65 if retyped else 0.4,
                        severity=None if retyped else Severity.LOW,
                        page=page.number,
                        evidence={"font": base, "subsets": tags, "shared": shared[:40]},
                    )
                )
        return findings

    def _isolated_fonts(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        in_document = Counter(c.fontname for page in doc.pages for c in visible_chars(page))
        for page in doc.pages:
            chars = visible_chars(page)
            if len(chars) < 50:
                continue
            counts = Counter(c.fontname for c in chars)
            dominant = counts.most_common(1)[0][1]
            for font, n in counts.items():
                if is_symbol_font(font) or n > max(40, 0.03 * len(chars)) or dominant < 5 * n:
                    continue
                if in_document[font] - n > max(40, n):
                    continue  # a font the document sets more text in on other pages
                if _styled(font) and any(
                    font_family(other) == font_family(font) for other in counts if other != font
                ):
                    continue  # a bold or italic cut of the page's own typeface, as totals use
                # Page numbers and running headers or footers often use their own font.
                margin = MARGIN_BAND * page.height
                own = [
                    c for c in chars
                    if c.fontname == font and margin < c.top and c.bottom < page.height - margin
                ]  # fmt: skip
                if not own:
                    continue
                numeric = sum(c.text in _NUMERIC for c in own) / len(own)
                words = [w for w in page.words if w.fontname == font]
                if numeric < 0.6 or not any(parse_money(w.text) is not None for w in words):
                    continue  # only amounts matter: a lone page number or code is not evidence
                box = BBox(
                    x0=min(c.x0 for c in own),
                    top=min(c.top for c in own),
                    x1=max(c.x1 for c in own),
                    bottom=max(c.bottom for c in own),
                )
                samples = [w.text for w in words][:6]
                findings.append(
                    self.finding(
                        "TL-FONT-002",
                        f"On page {page.number}, {_describe(font)} is used for "
                        f"only {n} characters, almost all digits ({', '.join(samples)}), while the "
                        "rest of the page uses other fonts. Numbers typed in afterwards often "
                        "look like this.",
                        confidence=0.5 + 0.4 * numeric,
                        page=page.number,
                        bbox=box,
                        evidence={"font": font, "characters": n, "values": samples},
                    )
                )
        return findings

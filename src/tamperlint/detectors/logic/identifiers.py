"""TL-LOGIC-004: identifiers with built-in checksums."""

from __future__ import annotations

import re

from tamperlint.detectors.base import Detector
from tamperlint.document import Document
from tamperlint.models import Finding, Layer

# IBAN lengths by country (ISO 13616 registry, common issuers).
IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22,
    "BR": 29, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20,
    "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27,
    "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27,
    "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15, "OM": 23,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31,
    "SE": 24, "SI": 19, "SK": 24, "SM": 27, "TN": 24, "TR": 26, "UA": 29, "VG": 24, "XK": 20,
}  # fmt: skip
# Only strings introduced by the word "IBAN" are checked, to avoid matching unrelated codes.
_IBAN_RE = re.compile(
    r"\biban(?:\s*(?:no\.?|number|#))?\s*[:#.\-]?\s*([a-z]{2}\d{2}[a-z0-9 \-]{11,45})",
    re.IGNORECASE,
)


def iban_is_valid(iban: str) -> bool:
    """ISO 13616 mod-97 check (and the country's length, when known)."""
    s = re.sub(r"[\s\-]", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    expected = IBAN_LENGTHS.get(s[:2])
    if expected is not None and len(s) != expected:
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def iban_candidate(raw: str) -> str | None:
    """Cut a captured IBAN to its country's length. The capture can run into the next words
    ("IBAN PK36 ... 0702 Opening balance"), so the known length decides where it ends."""
    compact = re.sub(r"[\s\-]", "", raw).upper()
    length = IBAN_LENGTHS.get(compact[:2])
    if length is None:
        return None  # unknown country: the end of the IBAN cannot be located reliably
    candidate = compact[:length]
    return candidate if len(candidate) == length and candidate.isalnum() else None


class IdentifierDetector(Detector):
    name = "identifiers"
    layer = Layer.LOGIC
    rule_ids = ("TL-LOGIC-004",)

    def applies(self, doc: Document) -> str | None:
        return None if any(p.words for p in doc.pages) else "no text layer"

    def run(self, doc: Document) -> list[Finding]:
        findings: list[Finding] = []
        for page in doc.pages:
            for m in _IBAN_RE.finditer(page.text):
                candidate = iban_candidate(m.group(1))
                if candidate is None or iban_is_valid(candidate):
                    continue
                findings.append(
                    self.finding(
                        "TL-LOGIC-004",
                        f"The IBAN {candidate} on page {page.number} fails its checksum, so at "
                        "least one character was changed or mistyped.",
                        confidence=0.8,
                        page=page.number,
                        evidence={"iban": candidate},
                    )
                )
        return findings

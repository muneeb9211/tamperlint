"""Guess the document type from its text, so content-logic checks know what to expect."""

from __future__ import annotations

import re

from tamperlint.document import Document

DOC_TYPES = ("statement", "invoice", "generic")

# Weighted signals. A title word ("invoice", "statement") outweighs vocabulary that both
# document types share: invoices often carry a payment block with an IBAN and an account
# number, and statements mention credits and debits.
_SIGNALS: dict[str, tuple[tuple[str, int], ...]] = {
    "statement": (
        (r"\b(account|bank) statement\b|\bstatement of account\b", 4),
        (r"\bstatement\b", 2),
        (r"\bopening balance\b|\bbalance brought forward\b", 3),
        (r"\bclosing balance\b|\bbalance carried forward\b", 3),
        (r"\bdebit\b|\bwithdrawals?\b", 1),
        (r"\bcredit\b|\bdeposits?\b", 1),
    ),
    "invoice": (
        (r"\b(tax )?invoice\b", 4),
        (r"\bsub-?\s?total\b", 2),
        (r"\b(total|amount|balance) due\b", 2),
        (r"\b(qty|quantity)\b", 2),
        (r"\bunit (price|cost)\b", 2),
        (r"\bbill(ed)? to\b", 1),
    ),
}
MIN_SCORE = 4


def detect_doc_type(doc: Document) -> str:
    text = doc.text.lower()
    if not text.strip():
        return "generic"
    scores = {
        kind: sum(weight for pattern, weight in signals if re.search(pattern, text))
        for kind, signals in _SIGNALS.items()
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < MIN_SCORE or list(scores.values()).count(scores[best]) > 1:
        return "generic"
    return best

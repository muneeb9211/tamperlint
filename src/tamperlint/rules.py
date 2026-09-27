"""The rule catalogue.

Every finding refers to a rule defined here. Rule IDs are stable across releases: a rule may be
refined or deprecated, but its ID is never reused for a different check.
"""

from __future__ import annotations

from dataclasses import dataclass

from tamperlint.models import Layer, Severity


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    title: str
    layer: Layer
    severity: Severity
    description: str
    false_positives: str


_RULES: tuple[Rule, ...] = (
    # ------------------------------------------------------------------ structure
    Rule(
        "TL-REV-001",
        "Document has incremental updates",
        Layer.STRUCTURE,
        Severity.INFO,
        "The PDF was saved more than once: later revisions were appended to the original file. "
        "Linearisation markers are not counted.",
        "Very common. E-signature services, form filling and many PDF tools append revisions. "
        "Informational only.",
    ),
    Rule(
        "TL-REV-002",
        "Later revision rewrote page content",
        Layer.STRUCTURE,
        Severity.HIGH,
        "A revision after the first one replaced the content stream of an existing page, so the "
        "visible text or graphics were changed after the document was first produced.",
        "Rare in genuine documents. Some tools rewrite pages when adding stamps or watermarks; "
        "check the changed page for a stamp.",
    ),
    Rule(
        "TL-REV-003",
        "Later revision added fonts to an existing page",
        Layer.STRUCTURE,
        Severity.MEDIUM,
        "A revision after the first one added font resources to a page that already existed. "
        "Editing text usually brings in a new font subset.",
        "Form filling can add fonts for field appearances; this is suppressed when a valid "
        "signature classifies the change as form filling.",
    ),
    Rule(
        "TL-REV-004",
        "Later revision added or removed pages",
        Layer.STRUCTURE,
        Severity.MEDIUM,
        "The number of pages changed in a revision after the first one: pages were inserted, "
        "appended or deleted after the document was produced.",
        "Appending a signature page or an attachment page in a later save.",
    ),
    Rule(
        "TL-SIG-001",
        "Signed and unmodified since signing",
        Layer.STRUCTURE,
        Severity.INFO,
        "A digital signature covers the document and no changes were made after signing. "
        "This is supporting evidence that the signed content is intact.",
        "The signer's identity is not verified against a trust list; this rule only concerns "
        "integrity.",
    ),
    Rule(
        "TL-SIG-002",
        "Changes after signing are not permitted",
        Layer.STRUCTURE,
        Severity.HIGH,
        "The document was changed after it was digitally signed, and the changes go beyond "
        "form filling, annotations or long-term-validation updates.",
        "Unusual workflows (for example, re-saving a signed file in an editor) trigger this "
        "rule; either way the signed version differs from the file you received.",
    ),
    Rule(
        "TL-SIG-003",
        "Signature integrity check failed",
        Layer.STRUCTURE,
        Severity.HIGH,
        "The bytes covered by a digital signature no longer match the signature.",
        "Corrupted downloads can also break signatures. Obtain the file again from the source.",
    ),
    Rule(
        "TL-SIG-004",
        "Permitted changes after signing",
        Layer.STRUCTURE,
        Severity.LOW,
        "The document was changed after signing in a way the signature allows (form filling, "
        "annotations or validation data).",
        "Normal for forms and multi-party signing.",
    ),
    Rule(
        "TL-META-001",
        "Editing tool recorded in metadata",
        Layer.STRUCTURE,
        Severity.MEDIUM,
        "The Producer, Creator or XMP history names a general-purpose PDF editor or online "
        "PDF service, which is unusual for documents issued by banks or institutions. Medium "
        "for statements and invoices, low for other documents.",
        "People legitimately compress, merge or design PDFs with online tools. Weak on its own.",
    ),
    Rule(
        "TL-META-002",
        "Metadata dates disagree",
        Layer.STRUCTURE,
        Severity.LOW,
        "The creation or modification date in the document information dictionary differs "
        "from the XMP metadata by more than a day.",
        "Time-zone handling and tools that update only one metadata store cause this.",
    ),
    Rule(
        "TL-META-003",
        "Modified before it was created",
        Layer.STRUCTURE,
        Severity.MEDIUM,
        "The recorded modification date is earlier than the creation date, both as written and "
        "after time-zone correction.",
        "Incorrect system clocks, and files converted long after their last edit. Dates that "
        "only look reversed because a tool labelled local time with the wrong zone are ignored.",
    ),
    Rule(
        "TL-META-004",
        "Producer changed between revisions",
        Layer.STRUCTURE,
        Severity.LOW,
        "Different revisions of the file were written by different software.",
        "Signing and form-filling software records its own producer.",
    ),
    Rule(
        "TL-ID-001",
        "Permanent document ID changed between revisions",
        Layer.STRUCTURE,
        Severity.MEDIUM,
        "The first part of the trailer /ID, which should stay constant for the life of a "
        "document, differs between revisions.",
        "Some tools regenerate IDs on every save.",
    ),
    # ------------------------------------------------------------------ content stream
    Rule(
        "TL-EDIT-001",
        "Acrobat text-edit markers present",
        Layer.CONTENT,
        Severity.HIGH,
        "The page contains /TouchUp_TextEdit marked-content sections, which Adobe Acrobat "
        "inserts when text is edited by hand.",
        "Genuine documents occasionally pass through Acrobat for corrections; the marker shows "
        "that text was edited, not by whom.",
    ),
    Rule(
        "TL-FONT-001",
        "Same font embedded as several subsets",
        Layer.CONTENT,
        Severity.MEDIUM,
        "On one page, the same base font appears under two unrelated subset prefixes that share "
        "characters (for example ABCDEF+Arial and GHIJKL+Arial), which happens when an editor "
        "embeds the font again to retype text. Medium when the extra subset holds a few digits "
        "(a retyped amount), low otherwise.",
        "Design and print workflows (Adobe, macOS Quartz, TeX) repeat subsets when they place "
        "artwork. Subsets numbered by one producer (LibreOffice BAAAAA, Microsoft Office "
        "BCDEEE) are ignored.",
    ),
    Rule(
        "TL-FONT-002",
        "Font used only for a few characters",
        Layer.CONTENT,
        Severity.MEDIUM,
        "A font is used for only a handful of characters on a page (often digits), while the "
        "surrounding text of the same style uses another font.",
        "Symbols, bullets and signatures legitimately use separate fonts; digits-only runs "
        "are weighted higher. Bold or italic cuts of the page's own typeface (totals) are "
        "ignored.",
    ),
    Rule(
        "TL-OVL-001",
        "Filled shape covers text",
        Layer.CONTENT,
        Severity.MEDIUM,
        "Text is hidden under a later filled shape (including white boxes drawn as paths), or "
        "an amount is overprinted by a different amount. Medium when a short piece of text is "
        "patched out by a box that hugs it and similar text is drawn in its place; other "
        "hidden text is reported as low.",
        "Panels, pictures and backgrounds placed over text in slides and designed pages "
        "(reported as low), and redaction boxes.",
    ),
    Rule(
        "TL-OVL-002",
        "Annotation placed over page text",
        Layer.CONTENT,
        Severity.MEDIUM,
        "A FreeText, Square or Stamp annotation overlaps existing page text.",
        "Reviewer comments and approval stamps.",
    ),
    Rule(
        "TL-OVL-003",
        "Invisible text on a page without a scanned image",
        Layer.CONTENT,
        Severity.LOW,
        "Text is drawn in invisible render mode on a page that has no full-page image, so it "
        "is not an OCR layer.",
        "Accessibility tags and search helpers.",
    ),
    # ------------------------------------------------------------------ geometry
    Rule(
        "TL-GEO-001",
        "Amount out of line with its row",
        Layer.GEOMETRY,
        Severity.MEDIUM,
        "An amount sits slightly (0.6 to 2 pt) above or below the baseline shared by the other "
        "words of its table row, a typical trace of a value retyped into place.",
        "Cells aligned differently on purpose. Prose, and pages where many values are off by "
        "varied amounts (an uneven layout), are not checked.",
    ),
    Rule(
        "TL-GEO-002",
        "Inconsistent typeface or size in a column of values",
        Layer.GEOMETRY,
        Severity.MEDIUM,
        "A value is rendered in a different typeface, or at a different size, from the other "
        "values in the same column. The page's own font decides which values are foreign, so "
        "a forger who retypes most of a column is still the one highlighted.",
        "Totals rows styled differently on purpose.",
    ),
    # ------------------------------------------------------------------ image
    Rule(
        "TL-IMG-001",
        "JPEG block grid broken in a region",
        Layer.IMAGE,
        Severity.MEDIUM,
        "The 8x8 compression grid of a JPEG image is misaligned or missing in one region, "
        "which happens when content is pasted in from another image. In PDFs only images of "
        "paper (scans) are checked, and the finding is a low-severity hint.",
        "Heavy recompression can erase the grid everywhere. Scanning and PDF software also "
        "recompress parts of a page, which is why the rule is only a hint inside PDFs.",
    ),
    Rule(
        "TL-IMG-002",
        "Duplicated image region",
        Layer.IMAGE,
        Severity.MEDIUM,
        "Two regions of the image are near-identical copies after excluding repeated "
        "characters, a trace of copy-move editing. Only wide bands and blocks are found: a copied "
        "word or a narrow column is missed.",
        "Logos and repeated form elements; identical glyphs are suppressed, and copies moved only "
        "sideways along a line are low-severity hints.",
    ),
    # ------------------------------------------------------------------ logic
    Rule(
        "TL-LOGIC-001",
        "Running balance does not reconcile",
        Layer.LOGIC,
        Severity.HIGH,
        "On a bank statement, previous balance plus credits minus debits does not equal the "
        "stated balance on one or more rows.",
        "Statements with pending transactions or multi-currency rows; the detector skips rows "
        "it cannot parse.",
    ),
    Rule(
        "TL-LOGIC-002",
        "Invoice totals do not add up",
        Layer.LOGIC,
        Severity.HIGH,
        "Line items, tax and discounts do not sum to the stated subtotal or total.",
        "Rounding rules; differences of up to two minor units (0.02) are ignored.",
    ),
    Rule(
        "TL-LOGIC-003",
        "Transaction date outside statement period",
        Layer.LOGIC,
        Severity.MEDIUM,
        "A transaction is dated before the statement period starts or after it ends.",
        "Value-dated transactions; a one-day tolerance is applied.",
    ),
    Rule(
        "TL-LOGIC-004",
        "Invalid IBAN checksum",
        Layer.LOGIC,
        Severity.MEDIUM,
        "An IBAN printed in the document fails the ISO 13616 mod-97 check.",
        "OCR or copy errors in the source text.",
    ),
)

RULES: dict[str, Rule] = {r.id: r for r in _RULES}
BUILTIN_RULE_IDS = frozenset(RULES)


def register_rule(rule: Rule) -> Rule:
    """Add a rule from a plugin. Plugin rule IDs must not use the built-in ``TL-`` prefix.

    Registering the same rule twice is allowed (modules can be imported more than once); a
    different rule under an existing ID is an error.
    """
    if rule.id.startswith("TL-") and rule.id not in BUILTIN_RULE_IDS:
        raise ValueError(f"{rule.id}: the TL- prefix is reserved for built-in rules")
    existing = RULES.get(rule.id)
    if existing is not None and existing != rule:
        raise ValueError(f"{rule.id} is already registered with a different definition")
    RULES[rule.id] = rule
    return rule


def get_rule(rule_id: str) -> Rule:
    try:
        return RULES[rule_id]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(
            f"Unknown rule id {rule_id!r}; built-in rules live in tamperlint.rules and plugins "
            "add theirs with tamperlint.rules.register_rule()"
        ) from exc

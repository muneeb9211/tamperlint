# Architecture

```
file (PDF / image)
  └─ loaders ──► Document
                  pages: positioned characters (with drawing order), words, shapes, annotations
                  revisions: page-content hashes, fonts, producer and IDs per incremental save
                  signatures: integrity, coverage and post-signing modification level (pyHanko)
                  metadata: Info dictionary, XMP, XMP history
                  rasters: input images and embedded JPEGs
  └─ detectors (plugins, one per group of rules) ──► findings
  └─ fusion (corroboration policy) ──► verdict + evidence score
  └─ reporters: console · JSON · SARIF · HTML
```

## Loading

`tamperlint.loaders.pdf` reads each PDF with three libraries, each for what it does best:

- **pikepdf** (qpdf) for revisions, trailer IDs, metadata and content-stream operators
  (Acrobat `/TouchUp_TextEdit` markers, invisible text mode).
- **pdfplumber** (pdfminer.six) for positioned characters, rectangles, images and annotations.
  The loader also recovers the *drawing order* between characters and rectangles, which
  pdfplumber keeps separately, to tell a box drawn over text from a table background drawn
  under it. Characters hidden by a later box or overprinted by later text are marked, and words
  are built from visible text only, so logic checks read what a person sees.
- **pyHanko** for signature integrity and the classification of changes made after signing.
  The signer's own certificate is the only trust root and nothing is fetched from the network:
  tamperlint checks integrity, not the signer's identity. Each signature is validated on its own,
  so one damaged signature cannot hide the others; certification (DocMDP) signatures are told
  apart from approval signatures.

Revisions come from the document's own cross-reference chain: every save ends with
`startxref <offset> %%EOF`, and only saves reachable from the last one through `/Prev` are
revisions. End-of-file markers inside attachments or comments, and the first-page marker of a
linearised file, are therefore ignored. Each revision is opened on its own (the file truncated at
its end), so tamperlint can say *which* page changed in *which* save, and when pages were added
or removed. An unreadable revision never hides a change: each readable revision is compared with
the last readable one before it.

Rectangle fills are checked for opacity and blend mode (ExtGState `/ca` and `/BM`, including in
Form XObjects), so a translucent highlight drawn over text is not mistaken for a cover-up.

Nothing executes PDF JavaScript. HTML previews are rendered with PDFium in the report writer
only.

## Detectors

| Module | Rules |
|---|---|
| `detectors/structure/signatures.py` | TL-SIG-001…004 |
| `detectors/structure/revisions.py` | TL-REV-001…004 |
| `detectors/structure/metadata.py` | TL-META-001…004, TL-ID-001 |
| `detectors/content/edits.py` | TL-EDIT-001 |
| `detectors/content/fonts.py` | TL-FONT-001…002 |
| `detectors/content/overlays.py` | TL-OVL-001…003 |
| `detectors/geometry/alignment.py` | TL-GEO-001…002 |
| `detectors/logic/statement.py`, `invoice.py`, `identifiers.py` | TL-LOGIC-001…004 |
| `detectors/image/jpeg_grid.py`, `copy_move.py` | TL-IMG-001…002 |

A detector declares which rules it owns, may decline to run (`applies()` returns a reason, shown
in reports), and must not raise for malformed input.

The content-logic detectors share `tamperlint/logic/columns.py`, which matches table values to
their header columns whatever the header alignment (left, right or centred over the numbers) and
treats multi-word headers such as "Price (incl. GST)" as one cell.

## Fusion

See `tamperlint/fusion.py`. Findings below confidence 0.4 are ignored for the verdict. The
evidence score is `1 - Π_rules(1 - max(weight(severity) × confidence))`: each rule counts once,
at its strongest finding, so one trace repeated many times does not inflate it. It summarises evidence strength and
is not a probability.

## Writing a detector plugin

```python
# my_package/detectors.py
from tamperlint.detectors.base import Detector
from tamperlint.models import Layer, Severity
from tamperlint.rules import Rule, register_rule


PAYSLIP_TAX = register_rule(
    Rule(
        "ACME-PAY-001",
        "Payslip tax does not match the rate table",
        Layer.LOGIC,
        Severity.MEDIUM,
        "The income tax on the payslip differs from the published rate table.",
        "Arrears, bonuses and mid-year rate changes.",
    )
)


class PayslipTaxDetector(Detector):
    name = "payslip-tax"
    layer = Layer.LOGIC
    rule_ids = (PAYSLIP_TAX.id,)

    def applies(self, doc):
        return None if "payslip" in doc.text.lower() else "not a payslip"

    def run(self, doc):
        return []  # build findings with self.finding(rule_id, message, confidence=..., page=...)
```

```toml
# pyproject.toml of the plugin package
[project.entry-points."tamperlint.detectors"]
payslip-tax = "my_package.detectors:PayslipTaxDetector"
```

Plugin rule IDs must not start with `TL-`, which is reserved for built-in rules. New built-in
rules need a description, documented false positives, and tests on the synthetic corpus for both
the forged and the benign cases.

## Synthetic data

`tamperlint.synth` builds genuine documents (fictional issuers, fake numbers, visible SPECIMEN
watermark), forgeries (cover-and-retype, recalculated balances, Acrobat-style markers, history
flattening, edits after signing, inflated totals, splicing, copy-move) and benign edits
(linearisation, re-saving, comments, signatures). The test suite and `evals/run_eval.py` use
these, so the full evaluation can be reproduced from source with no downloads.

Synthetic documents cannot show false alarms on real files. `evals/run_corpus.py` runs
tamperlint over any folder of real documents, with a time limit per file, and summarises the
verdicts and the rules behind them; [real-world-evaluation.md](real-world-evaluation.md) reports
its results on 1,000 web PDFs.

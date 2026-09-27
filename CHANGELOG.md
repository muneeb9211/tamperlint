# Changelog

All notable changes are documented here. The project follows
[Semantic Versioning](https://semver.org/); rule IDs are stable across releases.

## [0.1.0] - unreleased

### Added

- 27 rules across five layers: structure (revisions, added or removed pages, signatures,
  metadata, IDs), content (edit markers, fonts, overlays), geometry (baselines, column typefaces), logic (statement balances,
  invoice totals, statement dates, IBAN checksums) and image (JPEG grid, copy-move).
- Verdicts `INTACT`, `SUSPICIOUS` and `INCONCLUSIVE` with a conservative corroboration policy.
- Recovery of drawing order to detect text hidden under shapes or overprinted.
- Signature-aware analysis with pyHanko, including benign changes after signing.
- CLI (`check`, `rules`, `serve`, `demo-files`, `version`, `--version`), Python API, FastAPI
  service, Gradio demo, Dockerfile.
- `tamperlint.rules.register_rule()` for detector plugins that bring their own rules.
- Revisions are taken from the document's cross-reference chain, so end-of-file markers
  inside attachments do not create phantom revisions.
- Statement and invoice parsing that follows the table headers (multi-page statements, Dr/Cr
  markers, currency prefixes, several tax and charge lines, tax-inclusive totals).
- Console, JSON, SARIF 2.1.0 (GitHub code scanning) and self-contained HTML reports. Console and
  HTML group repeated findings of a rule on a page; JSON and SARIF keep every location.
- The evidence score counts each rule once, so one trace repeated many times does not inflate it.
- Synthetic corpus generators and a reproducible evaluation script.

### Hardened on real-world documents

Tested before release on 1,000 PDFs from the SafeDocs web corpus and on statements and invoices
printed by Microsoft Edge, Google Chrome and LibreOffice, then forged with PyMuPDF. Changes:

- Table columns are matched to their headers whether the headers are left-aligned, right-aligned
  or centred over the numbers, and multi-word headers ("Price (incl. GST)", "Balance (PKR)")
  count as one cell. Tax-inclusive totals ("Total (incl. GST)") are checked.
- Baseline checks (TL-GEO-001) only look at amounts in level table rows, with small offsets; an
  offset a template repeats across pages is layout.
- Hidden text (TL-OVL-001) is medium severity only when a short piece of text is patched out and
  similar text put in its place, or an amount is typed over an amount; design layering and text
  drawn twice are reported as low. Whiteout boxes drawn as paths (as PyMuPDF writes them) are
  recognised.
- Repeated font subsets (TL-FONT-001) must meet on one page; subsets numbered by one producer
  (LibreOffice, Microsoft Office) are ignored, and only a small subset of digits is medium.
- Isolated fonts (TL-FONT-002) ignore bold and italic cuts of the page's typeface and fonts the
  document uses on other pages. TeX fonts (CMR10, CMBX10) are recognised as one family.
- Column fonts (TL-GEO-002) are judged against the page's own font, so a forger who retypes most
  of a column is still the one highlighted.
- Editing-tool metadata (TL-META-001) is medium for statements and invoices and low elsewhere;
  modification dates that only look reversed because of a time-zone label (TL-META-003) are
  ignored.
- Pixel checks in PDFs only look at images of paper; JPEG grid evidence inside PDFs and copies
  that only move sideways are low-severity hints.
- Double-struck text is read once.
- Much faster on large and complex files: text and layout checks cover the first 50 pages, pages
  with very complex drawings are skipped with a note, and the hidden-text and baseline checks
  use spatial indexes.

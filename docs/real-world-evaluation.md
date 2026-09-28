# Real-world evaluation

The [synthetic evaluation](evaluation-results.md) shows that each check works as designed. It
cannot show how often tamperlint raises a false alarm on real documents, or whether it catches
edits made with real tools. Before the first release tamperlint was therefore run on two
real-world sets. Both runs used tamperlint 0.1.0.dev0 (the pre-release code in this
repository) on Windows 10 with Python 3.14.

## 1. False alarms on 1,000 PDFs from the web

**Set.** The first 1,000 files of the [SafeDocs corpus](https://digitalcorpora.org/corpora/file-corpora/cc-main-2021-31-pdf-untruncated/)
(CC-MAIN-2021-31-PDF-UNTRUNCATED, archive `0000.zip`): PDFs crawled from the public web in 2021.
They are reports, forms, brochures, papers, slides and scans, made by several hundred different
programs. None is known to be a forgery, but some were edited after they were first published.

**Results** (six worker processes on an 8-core laptop):

| Verdict | Files | Share |
|---|---:|---:|
| INTACT | 923 | 92.3% |
| INCONCLUSIVE | 47 | 4.7% |
| SUSPICIOUS | 24 | 2.4% |
| Could not be analysed | 6 | 0.6% |

Median time 2.1 s per file, 95th percentile 27 s; no file hit the three-minute limit. The six
files that could not be analysed were rejected with a clear message: one is password-protected,
two exceed the 50 MB limit, one is damaged and two are not PDFs at all.

**What the SUSPICIOUS files are.** Their findings were examined one by one:

- **23 record, in the file itself, that they were changed after they were first produced.** In
  18 a later revision rewrote a page (TL-REV-002), 8 carry Adobe Acrobat text-edit markers
  (TL-EDIT-001), 4 had pages added or removed in a later revision (TL-REV-004), and one was
  changed after it was signed (TL-SIG-002); some show several of these. tamperlint's statement
  about them is correct: the visible content was edited after publication. Whether the edit was
  innocent is for a reviewer to judge.
- **1 is a false alarm**: a document typeset in a legacy (non-Unicode) font, whose layered text
  reads as gibberish and looks patched over, with two uneven table values.

**INCONCLUSIVE** (review suggested): 43 files show a single weak signal, most often an amount off
its row's baseline (TL-GEO-001) or text hidden under a close-fitting box (TL-OVL-001); the other
4 record a change after publication but nothing else.

**What the test changed.** Before the fixes it prompted, a first pass over this set flagged
roughly one file in five as SUSPICIOUS and 16 files exceeded the time limit. Every false-alarm
pattern it revealed was fixed and is covered by a regression test; the
[changelog](../CHANGELOG.md) lists them.

## 2. Documents made with real software, then forged

**Genuine set.** 37 bank statements and invoices with fictional data and correct arithmetic,
printed to PDF by Microsoft Edge, Google Chrome and LibreOffice Writer (three statement and three
invoice layouts, six fonts, left-aligned and right-aligned headers, discounts, tax-inclusive
totals, balance due), plus one statement exported from a LibreOffice Calc spreadsheet.

**Forged set.** Each printed statement and invoice was then edited with
[PyMuPDF](https://pymupdf.readthedocs.io/) 1.28, a PDF library behind many editing tools, in four
ways. New text uses the original size and baseline, right-aligned like the original.

| Set | INTACT | INCONCLUSIVE | SUSPICIOUS |
|---|---:|---:|---:|
| Genuine, printed by Edge, Chrome and LibreOffice (37) | **37** | 0 | 0 |
| White box over one amount, new text on top, incremental save (36) | 0 | 0 | **36** |
| As above, every dependent total fixed too (24) | 0 | 0 | **24** |
| Amount removed by redaction, new text typed, file rewritten (36) | 0 | 0 | **36** |
| Amount removed, every total fixed, file rewritten (24) | 0 | **24** | 0 |

Before the fixes it prompted, 24 of the 37 genuine documents were flagged (column headers set
left of right-aligned numbers, multi-word headers, LibreOffice's numbered font subsets). The
forgeries were already flagged, but PyMuPDF draws its white boxes as paths, so the text they hid
was not reported until those paths were recognised.

The last row is the hardest case: the forger removes the original text, fixes every dependent
total so the arithmetic holds, and rewrites the whole file so no history is left. tamperlint
then reports the retyped values (set in a font the rest of the page does not use) as a single
kind of evidence, so the verdict asks for review rather than calling the file SUSPICIOUS, and
the report highlights exactly the values that were changed.

## Reproducing

`evals/run_corpus.py` runs tamperlint over a folder with a time limit per file and prints this
summary:

```bash
python evals/run_corpus.py path/to/safedocs results.jsonl --workers 6
```

The printed documents and their forged copies were made with scripts that drive Edge, Chrome and
LibreOffice and use PyMuPDF (AGPL-licensed), so they are not part of this Apache-licensed
repository; the method is described above.

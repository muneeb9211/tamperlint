# Contributing to tamperlint

Thanks for helping. The most valuable contributions are:

1. **False-positive reports.** A genuine document flagged as suspicious is a bug. Use the
   "False positive" issue template.
2. **New detectors or rules**, with tests for both forged and benign cases.
3. **Evaluation** on public research datasets, respecting their licences (most are
   evaluation-only; never commit their files).

## Never share personal data

Do not attach real bank statements, IDs, payslips or anything containing personal data to an
issue or pull request. Recreate the problem with `tamperlint demo-files`, with the generators in
`tamperlint.synth`, or with a document you made yourself. If only a real document reproduces the
issue, describe its structure (producer, number of revisions, the finding and rule ID) instead.

## Development setup

```bash
git clone <your fork>
cd tamperlint
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest                       # unit and end-to-end tests (about 30 s)
ruff check src tests && ruff format --check src tests
mypy
python evals/run_eval.py --seeds 5
```

## Adding a rule

- Add a `Rule` to `src/tamperlint/rules.py` with a stable ID (`TL-<LAYER>-NNN`), a description,
  and honest notes on known false positives. IDs are never reused.
- Implement it in a detector under `src/tamperlint/detectors/`, declaring the ID in `rule_ids`.
- Write messages for a non-expert reviewer: what was found, where, and why it matters.
- Add tests: at least one forged case that triggers the rule and the existing benign corpus must
  stay `INTACT`. Run the evaluation and include the before/after table in the pull request.
- Check false alarms on real documents: `python evals/run_corpus.py <folder> results.jsonl` on a
  folder of genuine PDFs you may process (for example part of the SafeDocs corpus), and report
  the rules that fire.
- Regenerate the reference: `tamperlint rules --markdown > docs/rules.md`.

## Style

Ruff (lint and format) and mypy in strict mode run in CI. Keep dependencies permissively
licensed: no GPL or AGPL dependencies (for example, PyMuPDF is AGPL and is not used).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

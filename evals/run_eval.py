"""Evaluate tamperlint on the synthetic corpus and write a results table.

    python evals/run_eval.py --seeds 10

Every document is generated from scratch by ``tamperlint.synth``: fictional issuers, fake
numbers, visible SPECIMEN watermark. Results on synthetic data are an upper bound for real
documents and are published as such. The "rebuilt" technique is included on purpose: it is a
forgery tamperlint is *not* expected to catch (see Limitations in the README).
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tamperlint import Verdict, __version__, check
from tamperlint.models import Severity
from tamperlint.rules import RULES
from tamperlint.synth import benign, forge, raster
from tamperlint.synth.genuine import make_invoice, make_statement

Case = Callable[[int], bytes]


@dataclass(frozen=True)
class Technique:
    name: str
    group: str  # "genuine", "benign-edit", "forged", "limitation"
    kind: str  # "pdf" or "scan"
    build: Case
    description: str


def _statement(seed: int) -> tuple[bytes, object]:
    return make_statement(seed)


def _row(truth: object, seed: int) -> int:
    rows = truth.rows  # type: ignore[attr-defined]
    return 2 + seed % max(1, len(rows) - 4)


def _edit(seed: int, **kw: object) -> bytes:
    pdf, truth = make_statement(seed)
    new = 12_345.67 + seed * 1_111
    return forge.statement_amount_edit(pdf, truth, _row(truth, seed), new, **kw)  # type: ignore[arg-type]


def _scan(pdf: bytes, seed: int) -> raster.np.ndarray:  # type: ignore[name-defined]
    return raster.scan(raster.render(pdf, dpi=150), seed=seed)


def _splice(seed: int) -> bytes:
    pdf, truth = make_statement(seed)
    host = raster.jpeg(_scan(pdf, seed), 80)
    cell = (
        truth.rows[_row(truth, seed)].cells.get("credit")
        or truth.rows[_row(truth, seed)].cells["debit"]
    )
    s = 150 / 72
    box = (
        int((cell.x_right - 70) * s) - 60,
        int((truth.page_height - cell.y - 12) * s) - 40,
        int((cell.x_right + 4) * s) + 60,
        int((truth.page_height - cell.y + 5) * s) + 40,
    )
    donor = _scan(make_statement(seed + 1000)[0], seed + 1000)
    return raster.png(raster.splice(host, donor, box))


def _copy_move(seed: int) -> bytes:
    gray = raster.decode(raster.jpeg(_scan(make_statement(seed)[0], seed), 80))
    h, w = gray.shape
    top = int(h * (0.33 + 0.02 * (seed % 3)))
    return raster.jpeg(
        raster.copy_move(gray, (80, top, w - 80, top + 110), (80, int(h * 0.62))), 90
    )


TECHNIQUES = [
    Technique("statement", "genuine", "pdf", lambda s: make_statement(s)[0], "Genuine statement"),
    Technique("invoice", "genuine", "pdf", lambda s: make_invoice(s)[0], "Genuine invoice"),
    Technique(
        "linearized",
        "benign-edit",
        "pdf",
        lambda s: benign.linearize(make_statement(s)[0]),
        "Fast-web-view optimisation",
    ),
    Technique(
        "resaved",
        "benign-edit",
        "pdf",
        lambda s: benign.full_resave(make_statement(s)[0]),
        "Full rewrite by another tool",
    ),
    Technique(
        "sticky-note",
        "benign-edit",
        "pdf",
        lambda s: benign.add_sticky_note(make_statement(s)[0]),
        "Reviewer comment added",
    ),
    Technique(
        "signed",
        "benign-edit",
        "pdf",
        lambda s: benign.sign(make_statement(s)[0]),
        "Digitally signed",
    ),
    Technique(
        "signed+note",
        "benign-edit",
        "pdf",
        lambda s: benign.add_sticky_note(benign.sign(make_statement(s)[0])),
        "Signed, then a comment added",
    ),
    Technique(
        "overlay",
        "forged",
        "pdf",
        _edit,
        "Amount covered and retyped (incremental save)",
    ),
    Technique(
        "overlay+recalc",
        "forged",
        "pdf",
        lambda s: _edit(s, recalc_balances=True),
        "As above, balances also corrected",
    ),
    Technique(
        "acrobat-style",
        "forged",
        "pdf",
        lambda s: _edit(s, touchup=True, producer="iLovePDF"),
        "Edit markers and editor producer",
    ),
    Technique(
        "flattened",
        "forged",
        "pdf",
        lambda s: benign.full_resave(_edit(s, baseline_shift=1.0)),
        "Edit, then history removed by re-saving",
    ),
    Technique(
        "signed-then-edited",
        "forged",
        "pdf",
        lambda s: forge.statement_amount_edit(
            benign.sign(make_statement(s)[0]),
            make_statement(s)[1],
            _row(make_statement(s)[1], s),
            9_999.0,
        ),
        "Edited after a digital signature",
    ),
    Technique(
        "invoice-total",
        "forged",
        "pdf",
        lambda s: make_invoice(s, total_override=make_invoice(s)[1].total + 5_000)[0],
        "Invoice total inflated at source",
    ),
    Technique(
        "scan",
        "genuine",
        "scan",
        lambda s: raster.jpeg(_scan(make_statement(s)[0], s), 70 + 5 * (s % 4)),
        "Genuine scan (JPEG q70-85)",
    ),
    Technique(
        "scan-invoice",
        "genuine",
        "scan",
        lambda s: raster.jpeg(_scan(make_invoice(s)[0], s), 80),
        "Genuine invoice scan",
    ),
    Technique(
        "splice", "forged", "scan", _splice, "Region pasted from another scan (saved as PNG)"
    ),
    Technique("copy-move", "forged", "scan", _copy_move, "Block of rows copied within the scan"),
    Technique(
        "rebuilt",
        "limitation",
        "pdf",
        lambda s: make_statement(s, overrides={3: {"credit": 99_999.0}})[0],
        "Rebuilt from scratch in the issuer's generator",
    ),
]


_SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2, Severity.INFO: 3}


def _top_rules(counts: dict[str, int], n: int = 4) -> list[str]:
    """Most frequent rules; ties go to the more severe rule, then the rule ID, so the table
    is the same on every run."""
    return sorted(counts, key=lambda r: (-counts[r], _SEVERITY_RANK[RULES[r].severity], r))[:n]


def run(seeds: int) -> dict[str, object]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rules: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    timings: list[float] = []
    for tech in TECHNIQUES:
        for seed in range(1, seeds + 1):
            data = tech.build(seed)
            start = time.perf_counter()
            report = check(
                data, name=f"{tech.name}-{seed}.{'pdf' if tech.kind == 'pdf' else 'img'}"
            )
            timings.append(time.perf_counter() - start)
            counts[tech.name][report.verdict.value] += 1
            for rule_id in {f.rule_id for f in report.findings if f.severity.value != "info"}:
                rules[tech.name][rule_id] += 1
    rows = []
    for tech in TECHNIQUES:
        c = counts[tech.name]
        rows.append(
            {
                "technique": tech.name,
                "group": tech.group,
                "input": tech.kind,
                "description": tech.description,
                "n": seeds,
                "suspicious": c[Verdict.SUSPICIOUS.value],
                "inconclusive": c[Verdict.INCONCLUSIVE.value],
                "intact": c[Verdict.INTACT.value],
                "top_rules": _top_rules(rules[tech.name]),
            }
        )
    return {
        "tamperlint": __version__,
        "python": platform.python_version(),
        "seeds": seeds,
        "median_seconds_per_file": sorted(timings)[len(timings) // 2],
        "rows": rows,
    }


def to_markdown(result: dict[str, object]) -> str:
    rows = result["rows"]  # type: ignore[assignment]
    lines = [
        f"Synthetic corpus, {result['seeds']} documents per technique, tamperlint "
        f"{result['tamperlint']}, median {result['median_seconds_per_file']:.2f} s per file.",
        "",
        "| Technique | Group | Input | SUSPICIOUS | INCONCLUSIVE | INTACT | Main rules |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for r in rows:  # type: ignore[attr-defined]
        lines.append(
            f"| {r['description']} | {r['group']} | {r['input']} | {r['suspicious']}/{r['n']} | "
            f"{r['inconclusive']}/{r['n']} | {r['intact']}/{r['n']} | "
            f"{', '.join(r['top_rules']) or '-'} |"
        )
    forged = [r for r in rows if r["group"] == "forged"]  # type: ignore[index]
    benign_rows = [r for r in rows if r["group"] in ("genuine", "benign-edit")]  # type: ignore[index]
    detected = sum(r["suspicious"] for r in forged)
    total_forged = sum(r["n"] for r in forged)
    false_pos = sum(r["suspicious"] for r in benign_rows)
    total_benign = sum(r["n"] for r in benign_rows)
    lines += [
        "",
        f"**Forgeries flagged SUSPICIOUS:** {detected}/{total_forged} "
        f"({detected / total_forged:.0%}). **Genuine or benign-edited documents flagged "
        f"SUSPICIOUS:** {false_pos}/{total_benign} ({false_pos / total_benign:.0%}).",
        "",
        "Scans cannot be called INTACT from pixels alone, so genuine scans are reported "
        "INCONCLUSIVE by design. The 'rebuilt' row is a known limitation, not a failure of a "
        "check.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "results")
    args = parser.parse_args()
    result = run(args.seeds)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    markdown = to_markdown(result)
    (args.out / "latest.md").write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()

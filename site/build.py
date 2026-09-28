"""Build the example-reports website published on GitHub Pages.

    pip install ".[synth]"
    python site/build.py _site

Every document is generated from scratch by ``tamperlint.synth`` (fictional issuers, SPECIMEN
watermark) and analysed by the tamperlint code in this checkout, so the published reports always
match the current rules. Nothing here is a real document.
"""

from __future__ import annotations

import html
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

from tamperlint import __version__, check
from tamperlint.models import Report, Severity
from tamperlint.report.html import render_html
from tamperlint.rules import RULES
from tamperlint.synth.samples import build_samples
from tamperlint.util import quiet_parser_logs

REPO = "https://github.com/muneeb9211/tamperlint"
THUMB_WIDTH = 360


@dataclass(frozen=True)
class Example:
    file: str
    title: str
    done: str


EXAMPLES = [
    Example(
        "statement_genuine.pdf",
        "Genuine bank statement",
        "Produced in one pass by the issuer's statement generator. Nothing was changed.",
    ),
    Example(
        "statement_signed.pdf",
        "Digitally signed statement",
        "The genuine statement with a digital signature added, a legitimate later change.",
    ),
    Example(
        "statement_edited.pdf",
        "One amount retyped",
        "A debit covered with a white box and a new amount typed on top in another font, "
        "saved as an incremental update.",
    ),
    Example(
        "statement_edited_flattened.pdf",
        "Expense hidden, balances fixed, history removed",
        "A large debit replaced by a smaller one, every later balance corrected so the "
        "arithmetic still holds, and the file re-saved to erase its edit history.",
    ),
    Example(
        "statement_signed_then_edited.pdf",
        "Edited after signing",
        "The signed statement with a debit retyped after the signature was applied.",
    ),
    Example(
        "invoice_genuine.pdf",
        "Genuine invoice",
        "Line items, tax and total produced by the issuer's invoicing software.",
    ),
    Example(
        "invoice_total_inflated.pdf",
        "Inflated invoice total",
        "The total raised above the sum of the lines and tax. The file carries no editing "
        "traces; only the arithmetic gives it away.",
    ),
    Example(
        "scan_genuine.jpg",
        "Genuine scan",
        "A scan of the genuine statement. Pixels alone cannot vouch for a document, so the "
        "verdict is INCONCLUSIVE by design.",
    ),
    Example(
        "scan_rows_copied.jpg",
        "Rows copied inside a scan",
        "A block of transaction rows copied and pasted further down the same scan.",
    ),
]


def thumbnail(name: str, data: bytes) -> bytes:
    from PIL import Image

    if name.endswith(".pdf"):
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(io.BytesIO(data))
        try:
            page = pdf[0]
            image = page.render(scale=THUMB_WIDTH * 2 / page.get_width()).to_pil()
        finally:
            pdf.close()
    else:
        image = Image.open(io.BytesIO(data))
    image = image.convert("RGB")
    image.thumbnail((THUMB_WIDTH * 2, THUMB_WIDTH * 3))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def evidence(report: Report) -> list[str]:
    """Rules behind the verdict: high and medium findings, strongest first, once each."""
    seen: list[str] = []
    for severity in (Severity.HIGH, Severity.MEDIUM):
        for finding in report.findings:
            if finding.severity is severity and finding.rule_id not in seen:
                seen.append(finding.rule_id)
    return seen


def card(example: Example, report: Report, stem: str) -> str:
    verdict = report.verdict.value
    rules = evidence(report)
    if rules:
        items = "".join(
            f"<li><code>{r}</code> {html.escape(RULES[r].title)}</li>" for r in rules[:4]
        )
        more = f'<li class="more">+ {len(rules) - 4} more</li>' if len(rules) > 4 else ""
        evidence_html = f'<ul class="rules">{items}{more}</ul>'
    else:
        evidence_html = '<p class="none">No evidence of tampering found.</p>'
    title = html.escape(example.title)
    return f"""
      <article class="card">
        <a class="thumb" href="reports/{stem}.html"
           aria-label="Open the report for {title}">
          <img src="thumbs/{stem}.png" alt="First page of {html.escape(example.file)}"
               loading="lazy" width="{THUMB_WIDTH}">
        </a>
        <div class="body">
          <div class="head">
            <span class="verdict {verdict.lower()}">{verdict}</span>
            <span class="score">evidence {report.score:.2f}</span>
          </div>
          <h3>{title}</h3>
          <p class="done">{html.escape(example.done)}</p>
          {evidence_html}
          <div class="links">
            <a class="button" href="reports/{stem}.html">Open report</a>
            <a class="button ghost" href="samples/{example.file}" download>Sample file</a>
          </div>
        </div>
      </article>"""


TEMPLATE = Path(__file__).with_name("template.html")


def main() -> None:
    quiet_parser_logs()
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "_site")
    for sub in ("reports", "samples", "thumbs"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    samples = build_samples()
    cards = []
    for example in EXAMPLES:
        data = samples[example.file]
        stem = Path(example.file).stem
        report = check(data, name=example.file)
        (out / "samples" / example.file).write_bytes(data)
        (out / "reports" / f"{stem}.html").write_text(render_html(report, data), encoding="utf-8")
        (out / "thumbs" / f"{stem}.png").write_bytes(thumbnail(example.file, data))
        cards.append(card(example, report, stem))
        print(f"{report.verdict.value:12s} {example.file}")
    sha = os.environ.get("GITHUB_SHA", "")[:7]
    build = f', built from commit <a href="{REPO}/commit/{sha}">{sha}</a>' if sha else ""
    page = Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        repo=REPO, version=html.escape(__version__), cards="".join(cards), build=build
    )
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"site written to {out}")


if __name__ == "__main__":
    main()

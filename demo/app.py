"""Gradio demo for Hugging Face Spaces.

Upload a PDF or a scan, or pick one of the SPECIMEN samples, and see the verdict with the edited
regions highlighted. Gradio writes each upload to its cache folder; the app deletes it as soon as
it has been read, and the cache sweep removes anything else (such as rejected file types).
"""

from __future__ import annotations

import html
from pathlib import Path

import gradio as gr

from tamperlint import __version__, check
from tamperlint.report.html import render_html
from tamperlint.synth.samples import build_samples
from tamperlint.util import quiet_parser_logs

quiet_parser_logs()
SAMPLES = build_samples(seed=1)
SAMPLE_LABELS = {
    "statement_genuine.pdf": "Genuine bank statement",
    "statement_edited.pdf": "Statement with one amount retyped",
    "statement_edited_flattened.pdf": "Retyped amounts, history removed, balances fixed",
    "statement_signed.pdf": "Digitally signed statement",
    "statement_signed_then_edited.pdf": "Signed, then edited",
    "invoice_total_inflated.pdf": "Invoice with an inflated total",
    "scan_genuine.jpg": "Genuine scan (JPEG)",
    "scan_rows_copied.jpg": "Scan with rows copied inside the image",
}
MAX_MB = 15
CACHE_SWEEP = (300, 300)  # every 5 minutes, delete cached files older than 5 minutes


def _iframe(report_html: str) -> str:
    return (
        '<iframe style="width:100%;height:1100px;border:0;border-radius:10px" '
        f'srcdoc="{html.escape(report_html, quote=True)}"></iframe>'
    )


def analyse_upload(path: str | None) -> tuple[str, str]:
    if not path:
        return "Upload a file or choose a sample.", ""
    file = Path(path)
    try:
        if file.stat().st_size > MAX_MB * 1024 * 1024:
            return f"Please upload a file smaller than {MAX_MB} MB.", ""
        data = file.read_bytes()
    finally:
        file.unlink(missing_ok=True)  # the document may be personal; keep no copy
    return _analyse(data, file.name)


def analyse_sample(label: str) -> tuple[str, str]:
    name = next(k for k, v in SAMPLE_LABELS.items() if v == label)
    return _analyse(SAMPLES[name], name)


def _analyse(data: bytes, name: str) -> tuple[str, str]:
    try:
        report = check(data, name=name)
    except Exception as exc:
        return f"Could not analyse this file: {exc}", ""
    headline = f"### {report.verdict.value}\n{report.summary}"
    return headline, _iframe(render_html(report, data))


with gr.Blocks(title="tamperlint: document tamper detection", delete_cache=CACHE_SWEEP) as demo:
    gr.Markdown(
        f"# tamperlint {__version__}\n"
        "Explainable tamper detection for PDFs and scanned documents: file structure, fonts, "
        "layout, pixels and arithmetic. Findings are evidence for a reviewer, not proof of fraud. "
        "**Uploads are deleted as soon as they are read.** Please do not upload documents you "
        "are not allowed to share. Samples are synthetic SPECIMEN "
        "documents from fictional issuers. Source code and documentation: "
        "[github.com/muneeb9211/tamperlint](https://github.com/muneeb9211/tamperlint)."
    )
    with gr.Row():
        upload = gr.File(
            label="PDF or image",
            file_types=[".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"],
            type="filepath",
        )
        sample = gr.Dropdown(
            choices=list(SAMPLE_LABELS.values()), value=None, label="…or try a sample"
        )
    headline = gr.Markdown()
    report = gr.HTML()
    upload.change(analyse_upload, inputs=upload, outputs=[headline, report])
    sample.change(analyse_sample, inputs=sample, outputs=[headline, report])

if __name__ == "__main__":
    demo.launch()

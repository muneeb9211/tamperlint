"""Command-line interface.

Exit codes: 0 = nothing at or above ``--fail-on``; 1 = at least one file reached ``--fail-on``;
2 = a file could not be analysed or the output could not be written.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tamperlint import __version__
from tamperlint.api import check as check_file
from tamperlint.models import Report, Verdict
from tamperlint.report import to_json
from tamperlint.report.console import render
from tamperlint.report.html import render_html
from tamperlint.report.sarif import to_sarif
from tamperlint.rules import RULES
from tamperlint.util import quiet_parser_logs

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Explainable tamper detection for PDFs and scanned documents.",
)
err = Console(stderr=True)


def configure_runtime() -> None:
    """Application-level settings a library must not impose on its callers."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # file names in any script must print on any console
            reconfigure(encoding="utf-8", errors="replace")
    quiet_parser_logs()


class OutputFormat(StrEnum):
    console = "console"
    json = "json"
    sarif = "sarif"
    html = "html"


class DocType(StrEnum):
    auto = "auto"
    statement = "statement"
    invoice = "invoice"
    generic = "generic"


class FailOn(StrEnum):
    suspicious = "suspicious"
    inconclusive = "inconclusive"
    never = "never"


def _fails(report: Report, fail_on: FailOn) -> bool:
    if fail_on is FailOn.never:
        return False
    if report.verdict is Verdict.SUSPICIOUS:
        return True
    return fail_on is FailOn.inconclusive and report.verdict is Verdict.INCONCLUSIVE


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Print the version."),
    ] = False,
) -> None:
    """Explainable tamper detection for PDFs and scanned documents."""
    configure_runtime()


def _is_closed_pipe(exc: OSError) -> bool:
    """The process reading our stdout closed it early (EPIPE; EINVAL on Windows)."""
    return isinstance(exc, BrokenPipeError) or exc.errno == errno.EINVAL


def _discard_stdout() -> None:
    """Point stdout at the null device so the interpreter's final flush does not fail."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def _artifact_uri(path: Path) -> str:
    """A SARIF artifact URI: relative to the working directory when possible (as GitHub code
    scanning expects), otherwise an absolute file URI."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_uri()


def _html_targets(paths: list[Path], output: Path | None) -> list[Path]:
    as_dir = output is not None and (
        len(paths) > 1
        or output.is_dir()
        or str(output).endswith(("/", os.sep))
        or not output.suffix
    )
    names: dict[str, int] = {}
    targets = []
    for path in paths:
        stem = path.stem
        names[stem] = names.get(stem, 0) + 1
        if names[stem] > 1:  # the same file name from different folders
            stem = f"{stem}-{names[stem]}"
        filename = f"{stem}.tamperlint.html"
        if output is None:
            targets.append(Path(filename))
        elif as_dir:
            targets.append(output / filename)
        else:
            targets.append(output)
    if as_dir and output is not None:
        output.mkdir(parents=True, exist_ok=True)
    return targets


@app.command("check")
def check_cmd(
    files: Annotated[
        list[Path],
        typer.Argument(help="PDF or image files to analyse.", exists=True, dir_okay=False),
    ],
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Output format.")
    ] = OutputFormat.console,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write to this file instead of stdout. For HTML, a directory when several "
            "files are given or the path has no extension.",
        ),
    ] = None,
    doc_type: Annotated[
        DocType, typer.Option("--type", "-t", help="Document type for the content checks.")
    ] = DocType.auto,
    fail_on: Annotated[
        FailOn, typer.Option(help="Verdict that makes the exit code non-zero.")
    ] = FailOn.suspicious,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show informational findings and skipped checks."),
    ] = False,
) -> None:
    """Analyse documents and report tampering evidence."""
    analysed: list[tuple[Path, bytes, Report]] = []
    had_error = False
    for path in files:
        try:
            data = path.read_bytes()
            report = check_file(data, name=path.name, doc_type=doc_type.value)
        except Exception as exc:
            had_error = True
            err.print(f"[red]error[/red] {escape(str(path))}: {escape(str(exc))}")
            continue
        analysed.append((path, data, report))
    reports = [r for _, _, r in analysed]

    try:
        _write_output(analysed, reports, output_format, output, verbose)
    except OSError as exc:
        if output is None and _is_closed_pipe(exc):
            _discard_stdout()  # e.g. `tamperlint check x.pdf | head`: the reader has left
        else:
            err.print(f"[red]error[/red] could not write the output: {escape(str(exc))}")
            raise typer.Exit(2) from exc

    if had_error:
        raise typer.Exit(2)
    if any(_fails(r, fail_on) for r in reports):
        raise typer.Exit(1)


def _write_output(
    analysed: list[tuple[Path, bytes, Report]],
    reports: list[Report],
    output_format: OutputFormat,
    output: Path | None,
    verbose: bool,
) -> None:
    if output_format in (OutputFormat.json, OutputFormat.sarif):
        if output_format is OutputFormat.json:
            text = to_json(reports)
        else:
            uris = [_artifact_uri(p) for p, _, _ in analysed]
            text = json.dumps(to_sarif(reports, uris), indent=2)
        if output:
            output.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)
    elif output_format is OutputFormat.html:
        targets = _html_targets([p for p, _, _ in analysed], output)
        for (path, data, report), target in zip(analysed, targets, strict=True):
            target.write_text(render_html(report, data), encoding="utf-8")
            err.print(
                f"{report.verdict.value:<12} {escape(path.name)} -> {escape(str(target))}",
                highlight=False,
                soft_wrap=True,
            )
    elif output:
        with output.open("w", encoding="utf-8") as handle:
            console = Console(file=handle, width=120)
            for report in reports:
                render(report, console, verbose=verbose)
    else:
        console = Console()
        for report in reports:
            render(report, console, verbose=verbose)


@app.command("rules")
def rules_cmd(
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print a Markdown reference.")
    ] = False,
) -> None:
    """List every rule tamperlint can report."""
    if markdown:
        typer.echo(rules_markdown())
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Rule")
    table.add_column("Layer")
    table.add_column("Severity")
    table.add_column("Title")
    for rule in RULES.values():
        table.add_row(rule.id, rule.layer.value, rule.severity.value, rule.title)
    Console().print(table)


def rules_markdown() -> str:
    lines = [
        "# Rule reference",
        "",
        "Generated by `tamperlint rules --markdown`. Rule IDs are stable across releases.",
        "",
        "| Rule | Layer | Default severity | Title |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| [{r.id}](#{r.id.lower()}) | {r.layer.value} | {r.severity.value} | {r.title} |"
        for r in RULES.values()
    ]
    for r in RULES.values():
        lines += ["", f"## {r.id}", "", f"**{r.title}** ({r.layer.value}, {r.severity.value})", ""]
        lines += [r.description, "", f"*Known false positives:* {r.false_positives}"]
    return "\n".join(lines) + "\n"


@app.command("serve")
def serve_cmd(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
) -> None:
    """Run the HTTP API (needs the 'server' extra)."""
    try:
        import uvicorn
    except ImportError as exc:
        err.print('The API needs the server extra: pip install "tamperlint\\[server]"')
        raise typer.Exit(2) from exc
    uvicorn.run("tamperlint.server.app:app", host=host, port=port)


@app.command("demo-files")
def demo_files_cmd(
    directory: Annotated[Path, typer.Argument(help="Where to write the sample documents.")] = Path(
        "tamperlint-samples"
    ),
) -> None:
    """Write a few genuine and forged SPECIMEN documents to try tamperlint on."""
    try:
        from tamperlint.synth.samples import write_samples
    except ImportError as exc:
        err.print('Sample generation needs the synth extra: pip install "tamperlint\\[synth]"')
        raise typer.Exit(2) from exc
    for path in write_samples(directory):
        typer.echo(path)


@app.command("version")
def version_cmd() -> None:
    """Print the version."""
    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()

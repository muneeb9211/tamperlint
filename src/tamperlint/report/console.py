"""Human-readable terminal output (Rich)."""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tamperlint.models import Report, Severity, Verdict
from tamperlint.report.grouping import group_findings

VERDICT_STYLE = {
    Verdict.INTACT: "bold green",
    Verdict.SUSPICIOUS: "bold red",
    Verdict.INCONCLUSIVE: "bold yellow",
}
SEVERITY_STYLE = {
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}


def render(report: Report, console: Console, *, verbose: bool = False) -> None:
    header = Text.assemble(
        (f"{report.verdict.value}", VERDICT_STYLE[report.verdict]),
        f"  {report.file.name}",
        (
            f"  |  {report.doc_type} | {report.file.pages} page(s) | evidence {report.score:.2f}",
            "dim",
        ),
    )
    parts: list[object] = [header, Text(report.summary)]

    shown = [f for f in report.findings if verbose or f.severity is not Severity.INFO]
    if shown:
        table = Table(show_header=True, header_style="bold", expand=True, box=None, pad_edge=False)
        table.add_column("Severity", no_wrap=True)
        table.add_column("Rule", no_wrap=True)
        table.add_column("Where", no_wrap=True)
        table.add_column("Finding", ratio=1)
        for group in group_findings(shown):
            f = group.lead
            where = []
            if f.page:
                where.append(f"p.{f.page}")
            if f.revision:
                where.append(f"rev {f.revision}")
            if group.count > 1:
                where.append(f"x{group.count}")
            message = Text(f.message)
            if group.count > 1:
                message.append(
                    f"\n+ {group.count - 1} more place(s) with the same finding"
                    + (f" on page {f.page}" if f.page else "")
                    + " (use --format json or html for each one).",
                    style="dim",
                )
            table.add_row(
                Text(f.severity.value, style=SEVERITY_STYLE[f.severity]),
                f.rule_id,
                " ".join(where) or "file",
                message,
            )
        parts += [Text(""), table]
    hidden = len(report.findings) - len(shown)
    if hidden:
        parts.append(
            Text(f"{hidden} informational finding(s) hidden; use --verbose to show.", style="dim")
        )

    skipped = [r for r in report.detectors if r.status != "ran"]
    if verbose and skipped:
        parts.append(Text(""))
        for r in skipped:
            parts.append(Text(f"- {r.name} {r.status}: {r.reason}", style="dim"))
    console.print(Panel(Group(*parts), title="tamperlint", title_align="left"))  # type: ignore[arg-type]

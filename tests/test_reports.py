"""SARIF and HTML output."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tamperlint import check
from tamperlint.cli import app
from tamperlint.report.html import render_html
from tamperlint.report.sarif import to_sarif
from tamperlint.rules import RULES


def test_sarif_meets_github_requirements(corpus: dict[str, bytes]) -> None:
    reports = [check(corpus["overlay_edit"], name="a.pdf"), check(corpus["genuine"], name="b.pdf")]
    log = to_sarif(reports, ["docs/a.pdf", "docs/b.pdf"])
    assert log["version"] == "2.1.0" and log["$schema"].endswith("sarif-2.1.0.json")
    run = log["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    assert {r["id"] for r in rules} == set(RULES)
    for rule in rules:
        assert rule["shortDescription"]["text"] and rule["fullDescription"]["text"]
        assert rule["help"]["text"]
    assert run["results"], "the forged file must produce results"
    for result in run["results"]:
        assert result["message"]["text"]
        assert result["partialFingerprints"]["tamperlintFinding/v1"]
        loc = result["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "docs/a.pdf"
        assert loc["region"]["startLine"] == 1
    fingerprints = [r["partialFingerprints"]["tamperlintFinding/v1"] for r in run["results"]]
    assert len(fingerprints) == len(set(fingerprints))
    verdicts = {v["uri"]: v["verdict"] for v in run["properties"]["verdicts"]}
    assert verdicts == {"docs/a.pdf": "SUSPICIOUS", "docs/b.pdf": "INTACT"}


def test_html_report_contains_findings_and_preview(corpus: dict[str, bytes]) -> None:
    data = corpus["overlay_edit"]
    html = render_html(check(data, name="forged.pdf"), data)
    assert "SUSPICIOUS" in html and "TL-LOGIC-001" in html
    assert "data:image/png;base64," in html
    assert 'class="box ' in html  # a highlighted region on the preview
    assert "<script" not in html  # static, no JavaScript


def test_html_escapes_untrusted_text(corpus: dict[str, bytes]) -> None:
    html = render_html(check(corpus["genuine"], name="<img src=x onerror=alert(1)>.pdf"))
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_cli_writes_sarif_and_html(tmp_path: Path, corpus: dict[str, bytes]) -> None:
    pdf = tmp_path / "forged.pdf"
    pdf.write_bytes(corpus["overlay_edit"])
    runner = CliRunner()
    sarif = tmp_path / "out.sarif"
    result = runner.invoke(app, ["check", str(pdf), "-f", "sarif", "-o", str(sarif)])
    assert result.exit_code == 1
    assert json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]["results"]
    html = tmp_path / "report.html"
    result = runner.invoke(
        app, ["check", str(pdf), "-f", "html", "-o", str(html), "--fail-on", "never"]
    )
    assert result.exit_code == 0
    assert "TL-REV-002" in html.read_text(encoding="utf-8")


def test_repeated_findings_are_grouped_for_people(corpus: dict[str, bytes]) -> None:
    from tamperlint.report.grouping import group_findings

    report = check(corpus["overlay_recalc"], name="recalc.pdf")
    covers = [f for f in report.findings if f.rule_id == "TL-OVL-001"]
    assert len(covers) > 5  # every covered amount is still its own finding in JSON and SARIF
    groups = group_findings(report.findings)
    assert len([g for g in groups if g.lead.rule_id == "TL-OVL-001"]) == 1
    html = render_html(report, corpus["overlay_recalc"])
    assert f"{len(covers)} places" in html
    assert html.count('class="box ') >= len(covers)  # every location is still highlighted


def test_repeated_rule_counts_once_in_the_score() -> None:
    from tamperlint.fusion import fuse
    from tamperlint.models import DetectorRun, Finding, Layer, Severity

    def cover(i: int) -> Finding:
        return Finding(
            rule_id="TL-OVL-001", title="t", layer=Layer.CONTENT, severity=Severity.MEDIUM,
            confidence=0.8, message=f"m{i}", page=1,
        )  # fmt: skip

    runs = [DetectorRun(name="a", layer=Layer.CONTENT, status="ran"),
            DetectorRun(name="b", layer=Layer.STRUCTURE, status="ran")]  # fmt: skip
    assert fuse([cover(i) for i in range(15)], runs).score == fuse([cover(0)], runs).score

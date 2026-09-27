from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tamperlint.cli import app

runner = CliRunner()


def _write(tmp_path: Path, corpus: dict[str, bytes], name: str) -> Path:
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(corpus[name])
    return path


def test_check_json_and_exit_codes(tmp_path: Path, corpus: dict[str, bytes]) -> None:
    genuine = _write(tmp_path, corpus, "genuine")
    forged = _write(tmp_path, corpus, "overlay_edit")

    ok = runner.invoke(app, ["check", str(genuine), "--format", "json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["verdict"] == "INTACT"

    bad = runner.invoke(app, ["check", str(forged), "--format", "json"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["verdict"] == "SUSPICIOUS"

    never = runner.invoke(app, ["check", str(forged), "--fail-on", "never"])
    assert never.exit_code == 0
    assert "SUSPICIOUS" in never.output


def test_unsupported_file(tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")
    result = runner.invoke(app, ["check", str(junk)])
    assert result.exit_code == 2


def test_rules_command() -> None:
    result = runner.invoke(app, ["rules"])
    assert result.exit_code == 0
    assert "TL-REV-002" in result.output


def test_unicode_file_name_and_version(tmp_path: Path, corpus: dict[str, bytes]) -> None:
    path = tmp_path / "Счёт_€_کھاتہ.pdf"
    path.write_bytes(corpus["genuine"])
    result = runner.invoke(app, ["check", str(path), "-f", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["file"]["name"] == path.name
    assert runner.invoke(app, ["--version"]).output.strip()


def test_invalid_document_type_is_rejected(tmp_path: Path, corpus: dict[str, bytes]) -> None:
    path = _write(tmp_path, corpus, "genuine")
    assert runner.invoke(app, ["check", str(path), "-t", "bogus"]).exit_code == 2


def test_html_output_directory_and_duplicate_names(
    tmp_path: Path, corpus: dict[str, bytes]
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = tmp_path / "a" / "doc.pdf", tmp_path / "b" / "doc.pdf"
    first.write_bytes(corpus["genuine"])
    second.write_bytes(corpus["overlay_edit"])
    out = tmp_path / "reports"  # no suffix: a directory, even for a single input
    result = runner.invoke(app, ["check", str(first), str(second), "-f", "html", "-o", str(out)])
    assert result.exit_code == 1
    assert sorted(p.name for p in out.iterdir()) == ["doc-2.tamperlint.html", "doc.tamperlint.html"]


def test_no_files_analysed_gives_an_empty_json_array(tmp_path: Path) -> None:
    junk = tmp_path / "broken.pdf"
    junk.write_bytes(b"%PDF-1.7 this is not really a pdf")
    result = runner.invoke(app, ["check", str(junk), "-f", "json"])
    assert result.exit_code == 2
    assert result.stdout.strip() == "[]"


def test_console_groups_repeated_findings(tmp_path: Path, corpus: dict[str, bytes]) -> None:
    path = _write(tmp_path, corpus, "overlay_recalc")
    result = runner.invoke(app, ["check", str(path), "--fail-on", "never"])
    assert result.exit_code == 0
    assert result.output.count("TL-OVL-001") == 1
    assert "more place(s) with the same finding" in result.output

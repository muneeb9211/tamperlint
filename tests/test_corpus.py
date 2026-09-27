"""End-to-end behaviour on the synthetic corpus: verdicts and the evidence behind them."""

from __future__ import annotations

import pytest

from tamperlint import Verdict, check

EXPECTED: dict[str, tuple[Verdict, set[str]]] = {
    # benign documents must stay INTACT (false-positive guard)
    "genuine": (Verdict.INTACT, set()),
    "linearized": (Verdict.INTACT, set()),
    "resaved": (Verdict.INTACT, set()),
    "sticky_note": (Verdict.INTACT, {"TL-REV-001"}),
    "signed": (Verdict.INTACT, {"TL-SIG-001"}),
    "signed_then_note": (Verdict.INTACT, {"TL-SIG-004"}),
    "invoice_genuine": (Verdict.INTACT, set()),
    # forgeries must be SUSPICIOUS, for the right reasons
    "overlay_edit": (
        Verdict.SUSPICIOUS,
        {"TL-REV-002", "TL-LOGIC-001", "TL-OVL-001", "TL-FONT-002"},
    ),
    "overlay_recalc": (Verdict.SUSPICIOUS, {"TL-REV-002", "TL-REV-003", "TL-OVL-001"}),
    "overlay_touchup_ilovepdf": (Verdict.SUSPICIOUS, {"TL-EDIT-001", "TL-META-001"}),
    "overlay_shift_flattened": (Verdict.SUSPICIOUS, {"TL-GEO-001", "TL-OVL-001", "TL-LOGIC-001"}),
    "signed_then_edited": (Verdict.SUSPICIOUS, {"TL-SIG-002", "TL-REV-002"}),
    "invoice_total_rebuilt": (Verdict.SUSPICIOUS, {"TL-LOGIC-002"}),
    # documented limitation: a consistent rebuild in the issuer's own generator is not detected
    "statement_rebuilt_edit": (Verdict.INTACT, set()),
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_verdicts(corpus: dict[str, bytes], name: str) -> None:
    report = check(corpus[name], name=f"{name}.pdf")
    verdict, rules = EXPECTED[name]
    found = {f.rule_id for f in report.findings}
    assert report.verdict is verdict, [(f.rule_id, f.message) for f in report.findings]
    assert rules <= found, f"missing {rules - found}"


def test_benign_documents_have_no_high_findings(corpus: dict[str, bytes]) -> None:
    for name in ("genuine", "linearized", "resaved", "sticky_note", "signed", "signed_then_note"):
        report = check(corpus[name], name=name)
        assert not [f for f in report.findings if f.severity.value in ("medium", "high")], name


def test_forged_findings_are_localised(corpus: dict[str, bytes]) -> None:
    report = check(corpus["overlay_edit"], name="overlay_edit.pdf")
    located = [f for f in report.findings if f.rule_id in ("TL-OVL-001", "TL-LOGIC-001")]
    assert located and all(f.page == 1 and f.bbox is not None for f in located)


def test_report_is_json_serialisable(corpus: dict[str, bytes]) -> None:
    report = check(corpus["overlay_edit"], name="overlay_edit.pdf")
    text = report.model_dump_json()
    assert '"verdict":"SUSPICIOUS"' in text
    assert report.file.sha256 and report.file.pages == 1

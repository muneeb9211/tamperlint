"""Unit tests for helpers, the rule catalogue and the fusion policy."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from tamperlint.detectors.logic.identifiers import iban_is_valid
from tamperlint.detectors.registry import BUILTIN_DETECTORS
from tamperlint.detectors.structure.metadata import match_editor
from tamperlint.document import strip_subset_prefix, subset_prefix
from tamperlint.fusion import fuse
from tamperlint.models import DetectorRun, Finding, Layer, Severity, Verdict
from tamperlint.rules import RULES
from tamperlint.synth.genuine import fake_iban
from tamperlint.text import font_family, is_bold, parse_money
from tamperlint.util import overlap_ratio, parse_pdf_date


def test_rule_ids_are_well_formed_and_owned() -> None:
    owned = [rid for det in BUILTIN_DETECTORS for rid in det.rule_ids]
    assert len(owned) == len(set(owned)), "a rule is owned by two detectors"
    assert set(owned) == set(RULES), "every rule needs exactly one detector and vice versa"
    assert all(re.fullmatch(r"TL-[A-Z]+-\d{3}", rid) for rid in RULES)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("D:20260915101500+05'00'", datetime(2026, 9, 15, 5, 15, tzinfo=UTC)),
        ("D:20260101", datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026-09-15T10:15:00Z", datetime(2026, 9, 15, 10, 15, tzinfo=UTC)),
        ("garbage", None),
        (None, None),
    ],
)
def test_parse_pdf_date(value: str | None, expected: datetime | None) -> None:
    assert parse_pdf_date(value) == expected


def test_overlap_ratio() -> None:
    assert overlap_ratio((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert overlap_ratio((0, 0, 10, 10), (5, 0, 15, 10)) == 0.5
    assert overlap_ratio((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,234.56", 1234.56),
        ("-80.00", -80.0),
        ("(12.50)", -12.5),
        ("PKR 1,000.00", 1000.0),
        ("2026", None),
        ("12.5", None),
        ("abc", None),
    ],
)
def test_parse_money(text: str, value: float | None) -> None:
    assert parse_money(text) == value


@pytest.mark.parametrize(
    ("font", "family"),
    [
        ("ABCDEF+ArialMT", "arial"),
        ("AAAAAA+BitstreamVeraSans-Bold", "bitstreamverasans"),
        ("Helvetica-BoldOblique", "helvetica"),
        ("TimesNewRomanPS-BoldMT", "timesnewroman"),
        ("GBHVFU+CMBX10", "cm"),  # TeX: bold, roman and italic cuts of Computer Modern
        ("CMR10", "cm"),
        ("SFTI1000", "sf"),
        ("Cambria", "cambria"),
    ],
)
def test_font_family(font: str, family: str) -> None:
    assert font_family(font) == family


@pytest.mark.parametrize(
    ("font", "bold"),
    [
        ("Arial-BoldMT", True),
        ("CMBX10", True),
        ("CMB10", True),
        ("CMR10", False),
        ("Cambria", False),
    ],
)
def test_is_bold(font: str, bold: bool) -> None:
    assert is_bold(font) is bold


def test_subset_prefix() -> None:
    assert strip_subset_prefix("ABCDEF+Arial") == "Arial"
    assert subset_prefix("ABCDEF+Arial") == "ABCDEF"
    assert subset_prefix("Arial") is None


def test_iban() -> None:
    assert iban_is_valid("GB82WEST12345698765432")
    assert iban_is_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_is_valid("GB82WEST12345698765433")
    import random

    for seed in range(20):
        assert iban_is_valid(fake_iban(random.Random(seed)))


def test_editor_matching() -> None:
    assert match_editor("iLovePDF") == "iLovePDF"
    assert match_editor("Smallpdf.com") == "Smallpdf"
    assert match_editor("Microsoft® Word for Microsoft 365") is None
    assert match_editor("Skia/PDF m128") is None


def _f(rule: str, layer: Layer, severity: Severity, confidence: float = 0.8) -> Finding:
    return Finding(
        rule_id=rule, title=rule, layer=layer, severity=severity, confidence=confidence, message="m"
    )


RUNS = [
    DetectorRun(name="a", layer=Layer.STRUCTURE, status="ran"),
    DetectorRun(name="b", layer=Layer.CONTENT, status="ran"),
]


def test_fusion_policy() -> None:
    assert fuse([], RUNS).verdict is Verdict.INTACT
    assert fuse([_f("X", Layer.STRUCTURE, Severity.HIGH)], RUNS).verdict is Verdict.SUSPICIOUS
    one_layer = [_f("X", Layer.CONTENT, Severity.MEDIUM), _f("Y", Layer.CONTENT, Severity.MEDIUM)]
    assert fuse(one_layer, RUNS).verdict is Verdict.INCONCLUSIVE
    two_layers = [_f("X", Layer.CONTENT, Severity.MEDIUM), _f("Y", Layer.GEOMETRY, Severity.MEDIUM)]
    assert fuse(two_layers, RUNS).verdict is Verdict.SUSPICIOUS
    weak = [_f("X", Layer.STRUCTURE, Severity.HIGH, confidence=0.2)]
    assert fuse(weak, RUNS).verdict is Verdict.INTACT
    too_few = [DetectorRun(name="a", layer=Layer.STRUCTURE, status="ran")]
    assert fuse([], too_few).verdict is Verdict.INCONCLUSIVE


def test_score_is_bounded() -> None:
    many = [_f(f"X{i}", Layer.STRUCTURE, Severity.HIGH, 1.0) for i in range(10)]
    result = fuse(many, RUNS)
    assert 0.99 <= result.score <= 1.0

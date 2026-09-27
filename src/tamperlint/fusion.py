"""Combine findings into a verdict.

The policy is deliberately conservative and easy to explain:

* **SUSPICIOUS** when there is at least one high-severity finding, or medium-severity findings
  from two different layers (independent kinds of evidence agree).
* **INCONCLUSIVE** when only a single layer produced medium-severity evidence, or when too few
  detectors could run for the absence of findings to mean anything.
* **INTACT** otherwise.

The score summarises evidence strength in ``[0, 1]``. It is *not* a calibrated probability.
"""

from __future__ import annotations

from dataclasses import dataclass

from tamperlint.models import DetectorRun, Finding, Layer, Severity, Verdict

_WEIGHT = {Severity.INFO: 0.0, Severity.LOW: 0.1, Severity.MEDIUM: 0.35, Severity.HIGH: 0.7}
MIN_CONFIDENCE = 0.4
MIN_LAYERS_FOR_INTACT = 2


@dataclass(frozen=True, slots=True)
class FusionResult:
    verdict: Verdict
    score: float
    summary: str


def fuse(findings: list[Finding], runs: list[DetectorRun]) -> FusionResult:
    considered = [f for f in findings if f.confidence >= MIN_CONFIDENCE]
    # Each rule counts once, at its strongest: fifteen covered amounts are one kind of evidence
    # seen fifteen times, not fifteen independent pieces of evidence.
    strongest: dict[str, float] = {}
    for f in considered:
        weight = _WEIGHT[f.severity] * f.confidence
        strongest[f.rule_id] = max(weight, strongest.get(f.rule_id, 0.0))
    remaining = 1.0
    for weight in strongest.values():
        remaining *= 1.0 - weight
    score = round(1.0 - remaining, 3)

    high = [f for f in considered if f.severity is Severity.HIGH]
    medium_layers = {f.layer for f in considered if f.severity is Severity.MEDIUM}
    ran_layers: set[Layer] = {r.layer for r in runs if r.status == "ran"}

    if high:
        rules = ", ".join(sorted({f.rule_id for f in high}))
        return FusionResult(
            Verdict.SUSPICIOUS, score, f"High-severity evidence of tampering ({rules})."
        )
    if len(medium_layers) >= 2:
        layers = " and ".join(sorted(layer.value for layer in medium_layers))
        return FusionResult(
            Verdict.SUSPICIOUS,
            score,
            f"Independent {layers} evidence points to editing after the document was produced.",
        )
    if medium_layers:
        (layer,) = medium_layers
        return FusionResult(
            Verdict.INCONCLUSIVE,
            score,
            f"Only {layer.value} evidence was found; review the findings before relying on "
            "this document.",
        )
    if len(ran_layers) < MIN_LAYERS_FOR_INTACT:
        return FusionResult(
            Verdict.INCONCLUSIVE,
            score,
            "Too few checks could run on this file to call it intact.",
        )
    return FusionResult(
        Verdict.INTACT, score, "No evidence of tampering was found by the checks that ran."
    )

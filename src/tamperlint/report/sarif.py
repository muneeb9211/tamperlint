"""SARIF 2.1.0 output, compatible with GitHub code scanning.

SARIF describes locations as lines and columns of text files. A PDF has pages and rectangles
instead, so every result points to line 1 of the document (GitHub requires a region) and the
real location (page, rectangle, revision) is carried in ``properties``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from tamperlint.models import Report, Severity
from tamperlint.rules import RULES, Rule
from tamperlint.version import __version__

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/muneeb9211/tamperlint"
_LEVEL = {
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}


def _semver(version: str) -> str:
    """PEP 440 to SemVer: "0.1.0.dev0" -> "0.1.0-dev.0", "1.2.0rc1" -> "1.2.0-rc.1"."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)\.?([a-z]+)?(\d+)?", version)
    if not match:
        return version
    core, tag, number = match.groups()
    return f"{core}-{tag}.{number or 0}" if tag else core


def _rule(rule: Rule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": "".join(part.capitalize() for part in rule.title.replace("-", " ").split()),
        "shortDescription": {"text": rule.title},
        "fullDescription": {"text": rule.description},
        "help": {
            "text": f"{rule.description}\n\nKnown false positives: {rule.false_positives}",
            "markdown": f"{rule.description}\n\n**Known false positives:** {rule.false_positives}",
        },
        "defaultConfiguration": {"level": _LEVEL[rule.severity]},
        "properties": {"layer": rule.layer.value, "tags": ["document-forensics", rule.layer.value]},
    }


def to_sarif(reports: list[Report], uris: list[str] | None = None) -> dict[str, Any]:
    """Build a SARIF log. ``uris`` are the artifact paths to report (defaults to file names)."""
    uris = uris or [r.file.name for r in reports]
    rule_index = {rule_id: i for i, rule_id in enumerate(RULES)}
    results: list[dict[str, Any]] = []
    for report, uri in zip(reports, uris, strict=True):
        for f in report.findings:
            box = f.bbox.model_dump() if f.bbox else None
            fingerprint = hashlib.sha256(
                json.dumps([report.file.sha256, f.rule_id, f.page, box, f.revision]).encode()
            ).hexdigest()
            results.append(
                {
                    "ruleId": f.rule_id,
                    "ruleIndex": rule_index[f.rule_id],
                    "level": _LEVEL[f.severity],
                    "message": {"text": f.message},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": uri},
                                "region": {
                                    "startLine": 1,
                                    "startColumn": 1,
                                    "endLine": 1,
                                    "endColumn": 1,
                                    "message": {
                                        "text": f"page {f.page}" if f.page else "whole document"
                                    },
                                },
                            }
                        }
                    ],
                    "partialFingerprints": {"tamperlintFinding/v1": fingerprint},
                    "properties": {
                        "severity": f.severity.value,
                        "confidence": f.confidence,
                        "layer": f.layer.value,
                        "page": f.page,
                        "bbox": box,
                        "revision": f.revision,
                        "evidence": f.evidence,
                    },
                }
            )
    return {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "tamperlint",
                        "version": __version__,
                        "semanticVersion": _semver(__version__),
                        "informationUri": INFORMATION_URI,
                        "rules": [_rule(rule) for rule in RULES.values()],
                    }
                },
                "artifacts": [
                    {
                        "location": {"uri": uri},
                        "hashes": {"sha-256": r.file.sha256},
                        "length": r.file.size_bytes,
                    }
                    for r, uri in zip(reports, uris, strict=True)
                ],
                "results": results,
                "properties": {
                    "verdicts": [
                        {
                            "uri": uri,
                            "verdict": r.verdict.value,
                            "score": r.score,
                            "summary": r.summary,
                        }
                        for r, uri in zip(reports, uris, strict=True)
                    ]
                },
            }
        ],
    }

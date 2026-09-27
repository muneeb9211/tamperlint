"""Group repeated findings for human-readable output.

A single edit technique often leaves the same trace in many places (for example fifteen
amounts covered by white boxes). Console and HTML reports show one entry per rule and page,
with every location still highlighted; JSON and SARIF keep one record per location.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tamperlint.models import Finding


@dataclass
class FindingGroup:
    """Findings of one rule on one page (or for the whole file), strongest first."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def lead(self) -> Finding:
        return self.findings[0]

    @property
    def count(self) -> int:
        return len(self.findings)

    @property
    def others(self) -> list[Finding]:
        return self.findings[1:]


def group_findings(findings: list[Finding]) -> list[FindingGroup]:
    """Group by (rule, page), keeping the order of each group's first finding.

    Findings arrive sorted by severity and confidence, so each group's lead is its strongest
    finding and the groups stay in order of importance.
    """
    groups: dict[tuple[str, int | None], FindingGroup] = {}
    for finding in findings:
        groups.setdefault((finding.rule_id, finding.page), FindingGroup()).findings.append(finding)
    return list(groups.values())

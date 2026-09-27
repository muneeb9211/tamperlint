"""Report writers: console, JSON, SARIF and HTML."""

from __future__ import annotations

from tamperlint.models import Report


def to_json(reports: list[Report], *, indent: int = 2) -> str:
    """Serialise one report as an object, and zero or several as an array."""
    if not reports:
        return "[]"
    if len(reports) == 1:
        return reports[0].model_dump_json(indent=indent)
    items = ",\n".join(r.model_dump_json(indent=indent) for r in reports)
    return f"[\n{items}\n]"

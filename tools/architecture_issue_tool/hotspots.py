"""Helpers that derive area-level hotspot stats from architecture violations."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tools.architecture_issue_tool.models import ArchitectureViolation

_TOP_HOTSPOTS = 10


def _area_for_violation(violation: ArchitectureViolation) -> str:
    evidence = violation.evidence
    for key in ("source_module", "source_unit", "package", "module"):
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("source_file", "path", "file"):
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            normalized = value.strip().replace("\\", "/")
            first = normalized.split("/", 1)[0].strip()
            if first:
                return first
    return "unknown"


def build_hotspots(
    violations: list[ArchitectureViolation],
    *,
    limit: int = _TOP_HOTSPOTS,
) -> list[dict[str, Any]]:
    """Rank architectural areas by violation count for report statistics."""
    if not violations:
        return []

    areas = [_area_for_violation(item) for item in violations]
    totals = Counter(areas)
    total = len(violations)
    by_area: dict[str, list[ArchitectureViolation]] = {area: [] for area in totals}
    for area, violation in zip(areas, violations, strict=True):
        by_area[area].append(violation)

    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
    hotspots: list[dict[str, Any]] = []
    for area, count in ranked:
        area_violations = by_area[area]
        severity_counts = {"p0": 0, "p1": 0, "p2": 0}
        kind_counts: dict[str, int] = {}
        for violation in area_violations:
            severity_counts[violation.severity] = severity_counts.get(violation.severity, 0) + 1
            kind_counts[violation.kind] = kind_counts.get(violation.kind, 0) + 1
        hotspots.append(
            {
                "area": area,
                "count": count,
                "share": round(count / total, 4),
                "severity_counts": severity_counts,
                "kind_counts": kind_counts,
            }
        )
    return hotspots

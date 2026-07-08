"""Miss recurrence analysis and benchmark scenario export."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from core.domain.feedback.misses.store import parse_timestamp
from core.domain.feedback.misses.taxonomy import MissRecord, MissTaxonomy


def grouping_key(row: MissRecord) -> tuple[str, str]:
    """Canonical ``(alert_name, taxonomy)`` key used to group misses.

    Both ``compute_recurrence`` and ``filter_top_misses`` go through this so
    the ``opensre misses stats`` recurring-pair view and the directory layout
    written by ``opensre misses export`` always agree on what counts as the
    same miss.
    """
    return (
        row.get("alert_name", "") or "<unknown>",
        row.get("taxonomy", "") or MissTaxonomy.UNKNOWN.value,
    )


def compute_recurrence(misses: list[MissRecord]) -> dict[tuple[str, str], int]:
    """Count misses grouped by ``(alert_name, taxonomy)``.

    A high count means the same alert keeps failing in the same way — the
    strongest signal that a regression scenario is warranted.
    """
    counter: Counter[tuple[str, str]] = Counter()
    for row in misses:
        counter[grouping_key(row)] += 1
    return dict(counter)


def compute_stats(misses: list[MissRecord]) -> dict[str, Any]:
    """Summary stats used by ``opensre misses stats`` and the docs reporter.

    Returns a dict with:
      - ``total``: total misses in scope
      - ``by_taxonomy``: count per taxonomy bucket
      - ``recurring``: top ``(alert_name, taxonomy)`` pairs seen more than once
      - ``unique_alerts``: distinct alert_names in scope
    """
    by_taxonomy: Counter[str] = Counter()
    by_alert: defaultdict[str, set[str]] = defaultdict(set)
    for row in misses:
        alert, taxonomy = grouping_key(row)
        by_taxonomy[taxonomy] += 1
        by_alert[alert].add(taxonomy)

    recurrence = compute_recurrence(misses)
    recurring = sorted(
        ((alert, tax, count) for (alert, tax), count in recurrence.items() if count > 1),
        key=lambda x: x[2],
        reverse=True,
    )

    return {
        "total": len(misses),
        "by_taxonomy": dict(by_taxonomy),
        "recurring": recurring,
        "unique_alerts": len(by_alert),
    }


def filter_top_misses(misses: list[MissRecord], top: int) -> list[MissRecord]:
    """Pick the ``top`` highest-priority misses for eval conversion.

    Priority order: most recurrent ``(alert_name, taxonomy)`` first; ties broken
    by recency. Returns one record per pair so the resulting eval set stays
    deduped — turning the *same* miss into five identical scenarios adds no
    coverage.
    """
    if top <= 0 or not misses:
        return []

    grouped: defaultdict[tuple[str, str], list[MissRecord]] = defaultdict(list)
    for row in misses:
        grouped[grouping_key(row)].append(row)

    representative: list[tuple[int, datetime, MissRecord]] = []
    for rows in grouped.values():
        rows.sort(key=lambda r: parse_timestamp(r.get("timestamp")), reverse=True)
        representative.append((len(rows), parse_timestamp(rows[0].get("timestamp")), rows[0]))

    representative.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [row for _, _, row in representative[:top]]


_SAFE_SLUG = re.compile(r"[^a-zA-Z0-9_.-]+")


def _slugify(value: str, *, fallback: str = "miss") -> str:
    cleaned = _SAFE_SLUG.sub("-", value).strip("-").lower()
    return cleaned or fallback


def to_benchmark_scenario(miss: MissRecord) -> dict[str, Any]:
    """Convert a miss into a benchmark scenario ``alert.json`` payload.

    The shape matches benchmark scenario ``alert.json`` payloads so the
    benchmark runner can consume the exported scenarios with no adapter changes.

    The grading rubric lives at ``commonAnnotations.scoring_points`` — that is
    where :func:`integrations.opensre.extract_scoring_points` looks for
    it (``opensre investigate --evaluate``), and where
    :func:`integrations.opensre.strip_scoring_points_from_alert` strips it before
    handing the alert to the agent. Putting it under ``_meta`` would both be
    invisible to the judge *and* leak the answer to the agent.
    """
    miss_id = miss.get("miss_id", str(uuid.uuid4()))
    alert_name = miss.get("alert_name") or "production miss"
    root_cause = miss.get("root_cause") or ""
    detail = miss.get("taxonomy_detail") or ""
    taxonomy = miss.get("taxonomy") or MissTaxonomy.UNKNOWN.value

    return {
        "_meta": {
            "purpose": "Regression scenario derived from a production miss",
            "source": "opensre misses export",
            "miss_id": miss_id,
            "original_run_id": miss.get("run_id", ""),
            "captured_at": miss.get("timestamp", ""),
            "taxonomy": taxonomy,
        },
        "title": f"[Regression] {alert_name}",
        "alert_name": alert_name,
        "pipeline_name": miss.get("pipeline_name", ""),
        "severity": miss.get("severity") or "warning",
        "alert_source": "closed_loop_learning",
        "message": detail or alert_name,
        "text": detail or alert_name,
        "commonLabels": {
            "pipeline_name": miss.get("pipeline_name", ""),
            "severity": miss.get("severity") or "warning",
            "taxonomy": taxonomy,
        },
        "commonAnnotations": {
            "summary": detail or alert_name,
            "miss_id": miss_id,
            "taxonomy": taxonomy,
            "scoring_points": {
                "expected_root_cause": root_cause,
                "expected_category": miss.get("root_cause_category", ""),
                "miss_notes": detail,
            },
        },
    }


def export_scenarios(
    misses: list[MissRecord],
    out_dir: Path,
) -> list[Path]:
    """Write one ``alert.json`` per miss under ``out_dir/<slug>/``.

    Returns the paths written. The caller is responsible for creating any
    enclosing benchmark config — this function only produces the per-case
    alert payloads that the existing runner already understands.
    """
    written: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    for index, miss in enumerate(misses, start=1):
        # ``or`` rather than dict.get default: a JSON null stored on disk
        # returns Python None, which would crash _slugify's re.sub.
        slug = _slugify(miss.get("alert_name") or "", fallback=f"miss-{index:04d}")
        taxonomy_slug = _slugify(miss.get("taxonomy") or "unknown", fallback="unknown")
        case_dir = out_dir / f"{index:04d}_{slug}_{taxonomy_slug}"
        case_dir.mkdir(parents=True, exist_ok=True)

        scenario = to_benchmark_scenario(miss)
        target = case_dir / "alert.json"
        target.write_text(
            json.dumps(scenario, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written.append(target)

    return written


__all__ = [
    "compute_recurrence",
    "compute_stats",
    "export_scenarios",
    "filter_top_misses",
    "grouping_key",
    "to_benchmark_scenario",
]

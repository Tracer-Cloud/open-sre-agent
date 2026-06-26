"""Closed-loop learning helpers for CLI feedback and miss triage.

The feedback prompt collects an accuracy rating after every investigation. When
a user marks a result as ``partial`` or ``inaccurate`` we additionally classify
the failure into a triage taxonomy and persist it to a separate ``misses.jsonl``
store. The ``opensre misses`` command group reads that store to surface trends,
recurrence, and reproducible benchmark scenarios.
"""

from __future__ import annotations

from cli.feedback.misses import (
    MissRecord,
    MissTaxonomy,
    compute_recurrence,
    compute_stats,
    load_misses,
    misses_path,
    record_miss,
    taxonomy_choices,
    to_benchmark_scenario,
)

__all__ = [
    "MissRecord",
    "MissTaxonomy",
    "compute_recurrence",
    "compute_stats",
    "load_misses",
    "misses_path",
    "record_miss",
    "taxonomy_choices",
    "to_benchmark_scenario",
]

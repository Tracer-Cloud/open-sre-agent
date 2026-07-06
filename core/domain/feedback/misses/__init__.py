"""Miss triage taxonomy, persistence, and conversion to benchmark scenarios.

A *miss* is an investigation whose user-facing rating was ``partial`` or
``inaccurate``. Each miss is classified into one of four root-cause buckets so
that recurring failure modes can be tracked over time and the worst offenders
can be replayed as regression scenarios in the benchmark suite.
"""

from core.domain.feedback.misses.export import (
    compute_recurrence,
    compute_stats,
    export_scenarios,
    filter_top_misses,
    to_benchmark_scenario,
)
from core.domain.feedback.misses.store import (
    load_misses,
    misses_path,
    parse_since,
    record_miss,
)
from core.domain.feedback.misses.taxonomy import (
    MissRecord,
    MissTaxonomy,
    taxonomy_choices,
)

__all__ = [
    "MissRecord",
    "MissTaxonomy",
    "compute_recurrence",
    "compute_stats",
    "export_scenarios",
    "filter_top_misses",
    "load_misses",
    "misses_path",
    "parse_since",
    "record_miss",
    "taxonomy_choices",
    "to_benchmark_scenario",
]

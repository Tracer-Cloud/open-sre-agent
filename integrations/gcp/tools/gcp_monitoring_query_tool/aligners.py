"""Aligner selection for Cloud Monitoring time series.

``timeSeries.list`` rejects an aligner that does not match the metric's kind:
``ALIGN_MEAN`` on a ``CUMULATIVE`` metric is a hard 400, not a degraded result.
Since most of what an investigation reaches for is cumulative — request counts,
``core_usage_time``, restart counts — a single hardcoded aligner fails on the
majority of useful queries.

The metric kind is a property of the metric descriptor, so it is looked up from
the metric type named in the caller's filter rather than guessed.
"""

from __future__ import annotations

import re
from typing import Any

#: ``metric.type="..."`` inside a monitoring filter. Single or double quoted.
_METRIC_TYPE_RE = re.compile(r"""metric\.type\s*=\s*["']([^"']+)["']""")

#: Kind → aligner. Rates rather than deltas for counters: a per-second rate is
#: comparable across alignment periods, a bucket delta is not.
_ALIGNER_BY_KIND = {
    "GAUGE": "ALIGN_MEAN",
    "DELTA": "ALIGN_RATE",
    "CUMULATIVE": "ALIGN_RATE",
}

#: Used when the kind is unknown, matching Cloud Monitoring's own default for
#: gauges — the most common kind when no descriptor could be read.
DEFAULT_ALIGNER = "ALIGN_MEAN"

#: Aligner valid for every kind, used to recover from a rejected aligner.
FALLBACK_ALIGNER = "ALIGN_RATE"


def extract_metric_type(monitoring_filter: str) -> str:
    """Return the metric type named in ``monitoring_filter``, or ``""``."""
    match = _METRIC_TYPE_RE.search(monitoring_filter or "")
    return match.group(1) if match else ""


def aligner_for_kind(metric_kind: str) -> str:
    """Map a ``metricKind`` to an aligner that the API will accept for it."""
    return _ALIGNER_BY_KIND.get((metric_kind or "").strip().upper(), DEFAULT_ALIGNER)


def describes_rejected_aligner(message: str) -> bool:
    """Return whether a 400 is specifically about the aligner being wrong."""
    return "perSeriesAligner" in (message or "")


def descriptor_kind(service: Any, project: str, metric_type: str) -> str:
    """Read ``metricKind`` from a metric descriptor; ``""`` when unavailable.

    Best-effort: a custom metric that has not reported yet has no descriptor,
    and some principals can read time series without reading descriptors.
    Neither should fail the query — the caller falls back to a retry.
    """
    if not metric_type:
        return ""
    try:
        descriptor = (
            service.projects()
            .metricDescriptors()
            .get(name=f"projects/{project}/metricDescriptors/{metric_type}")
            .execute()
        )
    except Exception:
        return ""
    return str(descriptor.get("metricKind", "")) if isinstance(descriptor, dict) else ""

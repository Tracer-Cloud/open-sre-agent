"""Cloud Monitoring time-series normalization.

A ``TimeSeries`` point holds its value in a ``TypedValue`` union — exactly one
of ``doubleValue``, ``int64Value``, ``boolValue``, ``stringValue`` or
``distributionValue`` is set, and which one depends on the metric's value type.
Reading only ``doubleValue`` works for CPU and latency gauges and silently
returns nulls for every ``INT64`` counter (request counts, restart counts),
which is most of what an SRE investigation asks for.
"""

from __future__ import annotations

from typing import Any

#: Points kept per series. Enough to see a trend and a step change without
#: spending the whole context budget on one metric.
_MAX_POINTS = 60


def point_value(typed: dict[str, Any]) -> Any:
    """Return the scalar inside a ``TypedValue``, or ``None`` if not scalar."""
    if not isinstance(typed, dict):
        return None
    for key in ("doubleValue", "int64Value", "boolValue", "stringValue"):
        if key in typed:
            value = typed[key]
            # int64 comes back as a JSON string — Google encodes 64-bit ints
            # that way because JSON numbers cannot hold them losslessly.
            if key == "int64Value":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return value
            return value
    distribution = typed.get("distributionValue")
    if isinstance(distribution, dict):
        return {
            "count": distribution.get("count"),
            "mean": distribution.get("mean"),
        }
    return None


def normalize_series(series: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ``TimeSeries`` into a compact, agent-readable dict."""
    metric = series.get("metric") or {}
    resource = series.get("resource") or {}
    raw_points = series.get("points") or []

    points = []
    for entry in raw_points[:_MAX_POINTS]:
        if not isinstance(entry, dict):
            continue
        interval = entry.get("interval") or {}
        points.append(
            {
                "time": interval.get("endTime", "") if isinstance(interval, dict) else "",
                "value": point_value(entry.get("value") or {}),
            }
        )

    normalized: dict[str, Any] = {
        "metric_type": metric.get("type", "") if isinstance(metric, dict) else "",
        "resource_type": resource.get("type", "") if isinstance(resource, dict) else "",
        "point_count": len(points),
        # Cloud Monitoring returns points newest-first; reverse so the agent
        # reads them in causal order when reasoning about a trend.
        "points": list(reversed(points)),
    }
    labels = metric.get("labels") if isinstance(metric, dict) else None
    if isinstance(labels, dict) and labels:
        normalized["metric_labels"] = labels
    resource_labels = resource.get("labels") if isinstance(resource, dict) else None
    if isinstance(resource_labels, dict) and resource_labels:
        normalized["resource_labels"] = resource_labels
    if len(raw_points) > _MAX_POINTS:
        normalized["truncated"] = True
    return normalized


def normalize_all(series_list: list[Any]) -> list[dict[str, Any]]:
    """Normalize a page of series, skipping anything that is not an object."""
    return [normalize_series(item) for item in series_list if isinstance(item, dict)]

"""Reading metrics and metric metadata from Yandex Monitoring.

Two quirks shape this module. Metric reads are a POST with the query in the
body, but the folder goes in the query string — putting it in the body is
accepted and then ignored, which reads as "no data" rather than an error. And
queries cannot span folders, so the folder is not an optional filter but the
scope of the whole call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from integrations.yandex_cloud.rest_client import YandexCloudClient

SERVICE: Final = "monitoring"

_DATA_READ_PATH: Final = "/monitoring/v2/data/read"
_METRIC_NAMES_PATH: Final = "/monitoring/v2/metrics/names"
_LABEL_KEYS_PATH: Final = "/monitoring/v2/metrics/labels"
_METRICS_PATH: Final = "/monitoring/v2/metrics"

#: Points per series. Enough shape to spot a trend or a spike without turning
#: an hour of one-second data into thousands of numbers in the prompt.
DEFAULT_MAX_POINTS: Final = 60
DEFAULT_WINDOW_MINUTES: Final = 60

_AGGREGATIONS: Final = frozenset({"AVG", "MAX", "MIN", "SUM", "LAST", "COUNT"})


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_window(from_time: str, to_time: str, window_minutes: int) -> tuple[str, str]:
    """Return the RFC3339 window to query, defaulting to the recent past."""
    end = to_time.strip() or _rfc3339(datetime.now(UTC))
    if from_time.strip():
        return from_time.strip(), end
    minutes = window_minutes if window_minutes > 0 else DEFAULT_WINDOW_MINUTES
    try:
        anchor = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        anchor = datetime.now(UTC)
    return _rfc3339(anchor - timedelta(minutes=minutes)), end


def summarize_series(series: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary of one time series.

    The agent almost always wants the shape — is it rising, how high did it
    get — rather than every point, and a full series of raw numbers crowds out
    the rest of the investigation.
    """
    timeseries = series.get("timeseries") or {}
    values = [
        value
        for value in (timeseries.get("doubleValues") or timeseries.get("int64Values") or [])
        if isinstance(value, int | float)
    ]
    summary: dict[str, Any] = {
        "name": series.get("name", ""),
        "labels": series.get("labels", {}),
        "type": series.get("type", ""),
        "points": len(values),
    }
    if values:
        summary |= {
            "min": min(values),
            "max": max(values),
            "avg": round(sum(values) / len(values), 4),
            "first": values[0],
            "last": values[-1],
        }
    return summary


class YandexMonitoringClient:
    """Reads metrics and metric metadata for one folder."""

    def __init__(self, client: YandexCloudClient) -> None:
        self._client = client

    @property
    def folder_id(self) -> str:
        return self._client.folder_id

    def read(
        self,
        query: str,
        *,
        from_time: str = "",
        to_time: str = "",
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
        aggregation: str = "AVG",
        max_points: int = DEFAULT_MAX_POINTS,
    ) -> dict[str, Any]:
        """Read metric data for a selector query."""
        start, end = resolve_window(from_time, to_time, window_minutes)
        grid_aggregation = aggregation.strip().upper() or "AVG"
        if grid_aggregation not in _AGGREGATIONS:
            return {
                "success": False,
                "service": SERVICE,
                "path": _DATA_READ_PATH,
                "data": None,
                "error": (
                    f"Unknown aggregation '{aggregation}'. "
                    f"Use one of: {', '.join(sorted(_AGGREGATIONS))}."
                ),
                "metadata": {},
            }
        body = {
            "query": query,
            "fromTime": start,
            "toTime": end,
            "downsampling": {
                "gridAggregation": grid_aggregation,
                # Gaps stay gaps. Yandex also offers PREVIOUS, which carries the
                # last value forward — a service that stopped reporting would
                # then read as a flat healthy line, which is the opposite of what
                # an investigation needs to see. NONE drops the points entirely,
                # which hides the outage just as effectively.
                "gapFilling": "NULL",
                # maxPoints, gridInterval and disabled are mutually exclusive in
                # the API; a point budget is the one that keeps a long window
                # from flooding the prompt.
                "maxPoints": str(max(1, max_points)),
            },
        }
        # folderId belongs in the query string; the body is not consulted for it.
        response = self._client.post(
            SERVICE, _DATA_READ_PATH, body, params={"folderId": self.folder_id}
        )
        response["metadata"] = {**response.get("metadata", {}), "from": start, "to": end}
        return response

    def metric_names(self, name_filter: str = "", selectors: str = "") -> dict[str, Any]:
        """List metric names within *selectors*.

        The scope matters more than it looks: with no selectors Yandex answers
        for its own services only, so a metric the user pushes themselves —
        anything under ``service="custom"`` — is absent from the list while
        being perfectly queryable. Discovery that cannot see a metric reads as
        "it does not exist", which is how an agent ends up telling someone their
        own metric is missing.

        ``nameFilter`` is sent for the day Yandex honours it; today the service
        ignores it, so callers must narrow the result themselves.
        """
        params: dict[str, Any] = {"folderId": self.folder_id}
        if selectors:
            params["selectors"] = selectors
        if name_filter:
            params["nameFilter"] = name_filter
        return self._client.get(SERVICE, _METRIC_NAMES_PATH, params, page_size=None)

    def label_keys(self, selectors: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"folderId": self.folder_id}
        if selectors:
            params["selectors"] = selectors
        return self._client.get(SERVICE, _LABEL_KEYS_PATH, params, page_size=None)

    def metrics(self, selectors: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"folderId": self.folder_id}
        if selectors:
            params["selectors"] = selectors
        return self._client.get(SERVICE, _METRICS_PATH, params)


__all__ = [
    "DEFAULT_MAX_POINTS",
    "DEFAULT_WINDOW_MINUTES",
    "SERVICE",
    "YandexMonitoringClient",
    "resolve_window",
    "summarize_series",
]

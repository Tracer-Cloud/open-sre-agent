"""Metric reads and metric discovery for Yandex Monitoring."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.monitoring_client import (
    DEFAULT_MAX_POINTS,
    DEFAULT_WINDOW_MINUTES,
    YandexMonitoringClient,
    summarize_series,
)

SOURCE = "yandex_cloud"

_QUERY_HELP = (
    'Yandex Monitoring query, e.g. \'cpu_usage{service="compute", resource_id="fhm..."}\'. '
    "This is NOT PromQL: `sum by (...)`, `rate(...)[5m]` and `topk(...)` are parse "
    "errors. Aggregate with Yandex functions instead — series_sum(...), "
    "series_avg(...), top_max(n, ...), alias(...), drop_nan(...). "
    "The folder label is `folder_id`; `folderId` silently matches nothing. "
    "Labels accept globs: * matches any value, | separates alternatives. "
    "Call list_yc_metrics first to discover names and labels, and pass "
    "selectors='service=\"custom\"' there for metrics the user pushes themselves."
)


def _monitoring_client(params: dict[str, Any]) -> YandexMonitoringClient | None:
    """Build the client from injected credentials, or None when unusable."""
    client = client_from_params(params)
    return None if client is None else YandexMonitoringClient(client)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


@tool(
    name="query_yc_metrics",
    surfaces=(ToolSurface.ACTION,),
    display_name="Yandex Monitoring",
    source=SOURCE,
    description=(
        "Read metric data from Yandex Monitoring for a selector query. Returns "
        "a summary per series — min, max, average, first and last — which is "
        "what establishes whether something was saturated, when it turned, and "
        "how far it moved. Queries cannot span folders."
    ),
    use_cases=[
        "Checking CPU, memory, or disk saturation on a VM or managed cluster",
        "Confirming whether a metric moved before or after an alert fired",
        "Comparing a metric across hosts to tell a single bad node from a fleet-wide problem",
        "Establishing the shape of a spike: how high, how long, still going",
    ],
    requires=["query"],
    outputs={
        "series": "one summary per matching time series",
        "series_count": "how many series matched",
        "window": "the time range actually queried",
    },
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": _QUERY_HELP},
            "from_time": {
                "type": "string",
                "description": (
                    "Start of the window, RFC3339. Defaults to window_minutes ago. "
                    "Set this for an incident on a known date: window_minutes counts "
                    "back from now and returns nothing for anything older."
                ),
                "default": "",
            },
            "to_time": {
                "type": "string",
                "description": "End of the window, RFC3339. Defaults to now.",
                "default": "",
            },
            "window_minutes": {
                "type": "integer",
                "description": "Window size when from_time is omitted.",
                "default": DEFAULT_WINDOW_MINUTES,
            },
            "aggregation": {
                "type": "string",
                "description": "How points are combined when downsampling.",
                "enum": ["AVG", "MAX", "MIN", "SUM", "LAST", "COUNT"],
                "default": "AVG",
            },
            "max_points": {
                "type": "integer",
                "description": "Points per series after downsampling.",
                "default": DEFAULT_MAX_POINTS,
            },
        },
        "required": ["query"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def query_yc_metrics(
    query: str,
    from_time: str = "",
    to_time: str = "",
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    aggregation: str = "AVG",
    max_points: int = DEFAULT_MAX_POINTS,
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read metric data from Yandex Monitoring."""
    if yc_backend is not None:
        response = dict(yc_backend.query_yc_metrics(query, from_time, to_time, window_minutes))
    else:
        client = _monitoring_client(credentials)
        if client is None:
            return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")
        response = client.read(
            query,
            from_time=from_time,
            to_time=to_time,
            window_minutes=window_minutes,
            aggregation=aggregation,
            max_points=max_points,
        )

    if not response.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": response.get("error", "Metric read failed."),
            "query": query,
        }

    data = response.get("data") or {}
    metrics = data.get("metrics") or []
    window = response.get("metadata", {})
    return {
        "source": SOURCE,
        "available": True,
        "query": query,
        "window": {"from": window.get("from", from_time), "to": window.get("to", to_time)},
        "series_count": len(metrics),
        "series": [summarize_series(series) for series in metrics],
    }


#: The shared response sanitizer caps any list at MAX_LIST_ITEMS and appends a
#: sentence saying so as the final element. Harmless in a cluster listing;
#: corrosive here, because discovery is the one place where absence is read as
#: proof - and because Yandex ignores nameFilter, so the narrowing happens after
#: the cut and cannot see what was removed.
_TRUNCATION_MARKER = "more items truncated"


def _has_more_pages(response: dict[str, Any]) -> bool:
    """Whether Yandex kept a continuation token back for this read."""
    return bool((response.get("metadata") or {}).get("next_page_token"))


def _split_off_truncation(values: list[Any]) -> tuple[list[str], bool]:
    """Return the real entries and whether the sanitizer dropped any."""
    kept = [str(value) for value in values if _TRUNCATION_MARKER not in str(value)]
    return kept, len(kept) != len(values)


#: Enough to show the shape of a selector without filling the prompt.
_SERIES_SAMPLE_SIZE = 3


def _series_sample(client: YandexMonitoringClient, selectors: str) -> list[dict[str, Any]]:
    """Return a few real series so a selector can be copied rather than guessed.

    Label keys alone are not enough to build a query: the folder-wide key list
    holds every key any service uses, so a caller reasonably picks ``cluster_id``
    for a managed database - which publishes under ``resource_id`` instead. The
    query then matches nothing, and matching nothing looks exactly like the
    metric not being collected.

    Only for a scoped call. Unscoped, Yandex answers with whichever series come
    first across the whole folder, so a question about one service is shown
    another one's labels - noise on every call, and misleading on the call where
    the caller is deciding what to trust.
    """
    if not selectors.strip():
        return []
    response = client.metrics(selectors)
    if not response.get("success"):
        return []
    listed = (response.get("data") or {}).get("metrics") or []
    return [
        {"name": entry.get("name", ""), "labels": entry.get("labels") or {}}
        for entry in listed[:_SERIES_SAMPLE_SIZE]
    ]


@tool(
    name="list_yc_metrics",
    surfaces=(ToolSurface.ACTION,),
    display_name="Yandex Monitoring",
    source=SOURCE,
    description=(
        "Discover what metrics exist in the folder, which labels they carry, and "
        "what those labels actually hold. Call before query_yc_metrics rather "
        "than guessing — a query that matches nothing returns no series, not an "
        "error, which reads exactly like the metric not being collected. Scope "
        "with a service selector: managed databases publish under names and "
        "labels of their own, so a name learned from one service rarely works "
        "for another."
    ),
    use_cases=[
        "Finding the metric name for a resource type",
        "Discovering which labels a metric can be filtered by, and their values",
        "Checking whether a service reports metrics at all",
    ],
    requires=[],
    outputs={
        "names": "matching metric names",
        "labels": "label keys available under the given selectors",
        "series_sample": "real series with their labels, when the call was scoped",
        "complete": "false when the list was cut short, so absence proves nothing",
    },
    input_schema={
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": "Only return metric names containing this text.",
                "default": "",
            },
            "selectors": {
                "type": "string",
                "description": (
                    "Scope the search, e.g. 'service=\"compute\"' or "
                    "'service=\"managed-greenplum\"'. Scoping is worth a call of its "
                    "own: it is the only way to see which labels the service really "
                    "uses. Metrics the user "
                    "pushes themselves live under 'service=\"custom\"' and are NOT in "
                    "the default listing — search there before concluding a metric "
                    "does not exist."
                ),
                "default": "",
            },
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def list_yc_metrics(
    name_filter: str = "",
    selectors: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """List metric names and label keys."""
    if yc_backend is not None:
        return dict(yc_backend.list_yc_metrics(name_filter, selectors))

    client = _monitoring_client(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    names_response = client.metric_names(name_filter, selectors)
    if not names_response.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": names_response.get("error", "Metric discovery failed."),
        }
    labels_response = client.label_keys(selectors)

    names, names_truncated = _split_off_truncation(
        (names_response.get("data") or {}).get("names") or []
    )
    # Two different ways the answer can be partial, and only one of them leaves a
    # marker: the sanitizer cuts at a hundred, while Yandex pages independently
    # and can hand back a short page with a token for the rest.
    names_truncated = names_truncated or _has_more_pages(names_response)
    listed_before_filter = len(names)
    # Yandex accepts nameFilter and ignores it, so narrowing has to happen here —
    # otherwise every filter returns the same list and the caller retries forever.
    needle = name_filter.strip().lower()
    if needle:
        names = [name for name in names if needle in name.lower()]
    labels, labels_truncated = _split_off_truncation(
        (labels_response.get("data") or {}).get("keys") or []
    )
    labels_truncated = labels_truncated or _has_more_pages(labels_response)
    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "scope": selectors or 'default (Yandex services; excludes service="custom")',
        "names": names,
        "labels": labels,
        "series_sample": _series_sample(client, selectors),
        "name_count": len(names),
        "complete": not (names_truncated or labels_truncated),
    }
    if names_truncated or labels_truncated:
        # Said as a field and not only in prose: a caller that reads "complete:
        # false" cannot conclude a metric is missing from this list, which is
        # exactly the wrong turn discovery invites.
        result["truncation_note"] = (
            f"Only {listed_before_filter} names were returned, and there are more. A metric "
            "absent from this list may still exist and be queryable - narrow with "
            'selectors, e.g. service="managed-postgresql", and search again.'
        )
    if not names and not selectors:
        # The most common reason for an empty result is scope, not absence.
        result["note"] = (
            "Nothing matched in the default scope, which does not include metrics "
            "the user pushes themselves. Retry with selectors='service=\"custom\"' "
            "before reporting the metric as missing."
        )
    return result


__all__ = ["list_yc_metrics", "query_yc_metrics"]

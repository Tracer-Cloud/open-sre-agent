"""Bounded read-only telemetry backend for the public ORCA environment."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from core.domain.types.incident_window import MAX_LOOKBACK_MINUTES
from core.domain.types.retrieval import TimeBounds


def _unix_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


_RELATIVE_TIME_RE = re.compile(r"^-(?P<amount>\d+)(?P<unit>[mhd])$")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _span_window_microseconds(span: dict[str, Any]) -> tuple[int, int] | None:
    try:
        start = int(span["startTime"])
        duration = int(span["duration"])
    except (KeyError, TypeError, ValueError):
        return None
    if start < 0 or duration < 0:
        return None
    return start, start + duration


def _span_overlaps_window(
    span: dict[str, Any],
    *,
    start_microseconds: int,
    end_microseconds: int,
) -> bool:
    span_window = _span_window_microseconds(span)
    if span_window is None:
        return False
    span_start, span_end = span_window
    return span_start <= end_microseconds and span_end >= start_microseconds


def _traces_in_window(
    traces: Any,
    *,
    start_microseconds: int,
    end_microseconds: int,
) -> list[dict[str, Any]]:
    if not isinstance(traces, list):
        return []
    bounded: list[dict[str, Any]] = []
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        spans = trace.get("spans")
        if not isinstance(spans, list):
            continue
        bounded_spans = [
            span
            for span in spans
            if isinstance(span, dict)
            and _span_overlaps_window(
                span,
                start_microseconds=start_microseconds,
                end_microseconds=end_microseconds,
            )
        ]
        if bounded_spans:
            bounded.append(_bounded_trace(trace, bounded_spans))
    return bounded


def _bounded_trace(
    trace: dict[str, Any],
    spans: list[dict[str, Any]],
) -> dict[str, Any]:
    kept_span_ids = {
        span["spanID"] for span in spans if isinstance(span.get("spanID"), str)
    }
    kept_process_ids = {
        span["processID"] for span in spans if isinstance(span.get("processID"), str)
    }
    bounded_spans = [_span_with_bounded_references(span, kept_span_ids) for span in spans]
    bounded: dict[str, Any] = {
        "traceID": trace.get("traceID"),
        "spans": bounded_spans,
    }
    processes = trace.get("processes")
    if isinstance(processes, dict):
        bounded["processes"] = {
            process_id: process
            for process_id, process in processes.items()
            if process_id in kept_process_ids
        }
    warnings = trace.get("warnings")
    if isinstance(warnings, list):
        bounded["warnings"] = warnings
    return bounded


def _span_with_bounded_references(
    span: dict[str, Any],
    kept_span_ids: set[str],
) -> dict[str, Any]:
    bounded_span = dict(span)
    references = span.get("references")
    if isinstance(references, list):
        bounded_span["references"] = [
            reference
            for reference in references
            if isinstance(reference, dict)
            and isinstance(reference.get("spanID"), str)
            and reference["spanID"] in kept_span_ids
        ]
    return bounded_span


@dataclass(frozen=True)
class OrcaTelemetryWindowPolicy:
    """Separate OpenSRE's default query window from the benchmark access horizon."""

    default_start_time: str
    default_end_time: str
    allowed_start_time: str
    allowed_end_time: str
    model_time_bounds: bool

    @classmethod
    def native(cls, *, start_time: str, end_time: str) -> "OrcaTelemetryWindowPolicy":
        """Keep native mode bounded to OpenSRE's resolved incident window."""
        return cls(
            default_start_time=start_time,
            default_end_time=end_time,
            allowed_start_time=start_time,
            allowed_end_time=end_time,
            model_time_bounds=False,
        )

    @classmethod
    def terminus_parity(
        cls,
        *,
        start_time: str,
        end_time: str,
    ) -> "OrcaTelemetryWindowPolicy":
        """Start with OpenSRE's default window but allow Terminus-like lookback."""
        current = _utc_datetime(end_time)
        allowed_start = current - timedelta(minutes=MAX_LOOKBACK_MINUTES)
        return cls(
            default_start_time=start_time,
            default_end_time=end_time,
            allowed_start_time=_iso_utc(allowed_start),
            allowed_end_time=end_time,
            model_time_bounds=True,
        )


class OrcaTelemetryBackend:
    """Expose ORCA's Prometheus, OpenSearch, and Jaeger through OpenSRE's backend API."""

    METRICS_UID = "webstore-metrics"
    LOGS_UID = "webstore-logs"
    TRACES_UID = "webstore-traces"

    def __init__(
        self,
        *,
        endpoint: str,
        username: str,
        password: str,
        verify_ssl: bool,
        start_time: str,
        end_time: str,
        window_policy: OrcaTelemetryWindowPolicy | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._auth = (username, password)
        self._verify = verify_ssl
        self._window_policy = window_policy or OrcaTelemetryWindowPolicy.native(
            start_time=start_time,
            end_time=end_time,
        )
        self._start = self._window_policy.default_start_time
        self._end = self._window_policy.default_end_time
        self._start_seconds = _unix_seconds(self._start)
        self._end_seconds = _unix_seconds(self._end)
        self._allowed_start = self._window_policy.allowed_start_time
        self._allowed_end = self._window_policy.allowed_end_time
        self._allowed_start_seconds = _unix_seconds(self._allowed_start)
        self._allowed_end_seconds = _unix_seconds(self._allowed_end)
        self._session = session or requests.Session()

    def _query_window(
        self,
        time_bounds: dict[str, Any] | TimeBounds | None,
    ) -> tuple[str, str, float, float]:
        """Resolve OpenSRE's native time controls against ORCA's simulated clock."""
        if time_bounds is None or not self._window_policy.model_time_bounds:
            return self._start, self._end, self._start_seconds, self._end_seconds

        bounds = TimeBounds.model_validate(time_bounds)
        end = self._resolve_time(bounds.end_time, anchor=self._end)
        if bounds.start_time:
            start = self._resolve_time(bounds.start_time, anchor=_iso_utc(end))
        elif bounds.lookback_minutes is not None:
            start = end - timedelta(minutes=bounds.lookback_minutes)
        else:
            default_span = timedelta(seconds=self._end_seconds - self._start_seconds)
            start = end - default_span

        allowed_start = datetime.fromtimestamp(self._allowed_start_seconds, tz=UTC)
        allowed_end = datetime.fromtimestamp(self._allowed_end_seconds, tz=UTC)
        if end > allowed_end:
            raise ValueError(
                "Telemetry time bounds cannot extend past ORCA's simulated current time"
            )
        if start < allowed_start:
            raise ValueError(
                "Telemetry time bounds cannot start before ORCA's allowed telemetry window"
            )
        if start > end:
            raise ValueError("Telemetry time bounds require start_time <= end_time")
        return _iso_utc(start), _iso_utc(end), start.timestamp(), end.timestamp()

    @staticmethod
    def _resolve_time(value: str | None, *, anchor: str) -> datetime:
        if value is None or value == "now":
            return datetime.fromisoformat(anchor.replace("Z", "+00:00")).astimezone(UTC)
        relative = _RELATIVE_TIME_RE.fullmatch(value)
        if relative:
            amount = int(relative.group("amount"))
            delta = {
                "m": timedelta(minutes=amount),
                "h": timedelta(hours=amount),
                "d": timedelta(days=amount),
            }[relative.group("unit")]
            return datetime.fromisoformat(anchor.replace("Z", "+00:00")).astimezone(UTC) - delta
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _url(self, uid: str, path: str) -> str:
        return f"{self._endpoint}/api/datasources/proxy/uid/{uid}{path}"

    def _get(self, uid: str, path: str, **kwargs: Any) -> Any:
        response = self._session.get(
            self._url(uid, path),
            auth=self._auth,
            verify=self._verify,
            timeout=20,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def _grafana_get(self, path: str, **kwargs: Any) -> Any:
        response = self._session.get(
            f"{self._endpoint}{path}",
            auth=self._auth,
            verify=self._verify,
            timeout=20,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def query_timeseries(
        self,
        query: str = "",
        *,
        time_bounds: dict[str, Any] | TimeBounds | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Execute a bounded Prometheus range query at the task's simulated time."""
        _start, _end, start_seconds, end_seconds = self._query_window(time_bounds)
        return self._get(
            self.METRICS_UID,
            "/api/v1/query_range",
            params={
                "query": query,
                "start": start_seconds,
                "end": end_seconds,
                "step": 60,
            },
        )

    def query_logs(
        self,
        service_name: str = "",
        *,
        limit: int = 20,
        time_bounds: dict[str, Any] | TimeBounds | None = None,
        query: str | None = None,
        sort_order: str = "asc",
        cursor: list[Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Search bounded OpenSearch log documents and return a Loki-compatible shape."""
        if not service_name:
            return {
                "status": "success",
                "data": {"resultType": "streams", "result": []},
            }
        start, end, _start_seconds, _end_seconds = self._query_window(time_bounds)
        filters: list[dict[str, Any]] = [
            {"range": {"@timestamp": {"gte": start, "lte": end}}}
        ]
        filters.append({"term": {"resource.service.name.keyword": service_name}})
        effective_limit = min(max(1, limit), 1000)
        if sort_order not in {"asc", "desc"}:
            raise ValueError("Log sort_order must be 'asc' or 'desc'")
        bool_query: dict[str, Any] = {"filter": filters}
        if query and query.strip() and query.strip() != "*":
            bool_query["must"] = [{"query_string": {"query": query.strip()}}]
        body = {
            "size": effective_limit,
            "sort": [
                {"@timestamp": sort_order},
                {"_id": sort_order},
            ],
            "query": {"bool": bool_query},
        }
        if cursor:
            body["search_after"] = cursor
        response = self._session.post(
            self._url(self.LOGS_UID, "/otel-logs-*/_search"),
            auth=self._auth,
            verify=self._verify,
            timeout=20,
            json=body,
        )
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
        streams: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source", {})
            timestamp = str(source.get("@timestamp", ""))
            timestamp_ns = str(int(_unix_seconds(timestamp) * 1_000_000_000))
            message = source.get("body") or source.get("message") or json.dumps(source)
            actual_service = source.get("resource.service.name") or service_name
            attributes = {
                key: value
                for key, value in source.items()
                if key not in {"@timestamp", "body", "message"}
            }
            streams.append(
                {
                    "stream": {
                        "service_name": str(actual_service),
                        "log_level": str(source.get("severity.text", "")),
                        "attributes": attributes,
                        "document_id": str(hit.get("_id", "")),
                    },
                    "values": [[timestamp_ns, str(message)]],
                }
            )
        return {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": streams,
            },
            "next_cursor": hits[-1].get("sort") if len(hits) == effective_limit else None,
        }

    def query_service_names(
        self,
        *,
        time_bounds: dict[str, Any] | TimeBounds | None = None,
    ) -> list[str]:
        """Return service names represented in bounded OpenSearch log documents."""
        start, end, _start_seconds, _end_seconds = self._query_window(time_bounds)
        body = {
            "size": 0,
            "query": {
                "range": {"@timestamp": {"gte": start, "lte": end}}
            },
            "aggs": {
                "services": {
                    "terms": {"field": "resource.service.name.keyword", "size": 100}
                }
            },
        }
        response = self._session.post(
            self._url(self.LOGS_UID, "/otel-logs-*/_search"),
            auth=self._auth,
            verify=self._verify,
            timeout=20,
            json=body,
        )
        response.raise_for_status()
        buckets = response.json().get("aggregations", {}).get("services", {}).get("buckets", [])
        return sorted(str(bucket["key"]) for bucket in buckets if bucket.get("key"))

    def query_alert_rules(self) -> dict[str, Any]:
        """Return Grafana ruler data in the backend-normalization shape."""
        raw = self._grafana_get("/api/ruler/grafana/api/v1/rules")
        groups: list[dict[str, Any]] = []
        for folder, folder_groups in raw.items():
            for group in folder_groups:
                rules = []
                for rule in group.get("rules", []):
                    alert = rule.get("grafana_alert", {})
                    rules.append(
                        {
                            "name": alert.get("title", "unknown"),
                            "state": alert.get("current_state", ""),
                            "noDataState": alert.get("no_data_state"),
                            "queries": alert.get("data", []),
                            "annotations": rule.get("annotations", {}),
                            "labels": rule.get("labels", {}),
                        }
                    )
                groups.append(
                    {"folder": folder, "name": group.get("name", ""), "rules": rules}
                )
        return {"groups": groups}

    def query_annotations(
        self,
        *,
        tags: list[str] | None = None,
        limit: int = 100,
        time_bounds: dict[str, Any] | TimeBounds | None = None,
    ) -> list[dict[str, Any]]:
        """Query Grafana annotations using the same explicit task window."""
        _start, _end, start_seconds, end_seconds = self._query_window(time_bounds)
        params: list[tuple[str, Any]] = [
            ("from", int(start_seconds * 1000)),
            ("to", int(end_seconds * 1000)),
            ("limit", limit),
        ]
        params.extend(("tags", tag) for tag in tags or [])
        payload = self._grafana_get("/api/annotations", params=params)
        return payload if isinstance(payload, list) else []

    def query_traces(
        self,
        service_name: str = "",
        *,
        limit: int = 20,
        time_bounds: dict[str, Any] | TimeBounds | None = None,
        action: str = "search",
        trace_id: str | None = None,
        operation: str | None = None,
        tags: dict[str, Any] | None = None,
        min_duration: str | None = None,
        max_duration: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Query bounded Jaeger traces for a concrete service."""
        if action not in {"search", "get_trace"}:
            raise ValueError("Trace action must be 'search' or 'get_trace'")
        start, end, start_seconds, end_seconds = self._query_window(time_bounds)
        if action == "get_trace":
            if not trace_id:
                raise ValueError("trace_id is required for get_trace")
            payload = self._get(self.TRACES_UID, f"/api/traces/{trace_id}")
            return {
                "traces": _traces_in_window(
                    payload.get("data", []),
                    start_microseconds=int(start_seconds * 1_000_000),
                    end_microseconds=int(end_seconds * 1_000_000),
                ),
                "metrics": {},
                "query_window": {"start": start, "end": end},
            }
        if not service_name:
            return {"traces": [], "metrics": {}, "query_window": self.query_window}
        effective_limit = min(max(1, limit), 1000)
        params: dict[str, Any] = {
            "service": service_name,
            "start": int(start_seconds * 1_000_000),
            "end": int(end_seconds * 1_000_000),
            "limit": effective_limit,
        }
        if operation:
            params["operation"] = operation
        if tags:
            params["tags"] = json.dumps(tags, sort_keys=True, separators=(",", ":"))
        if min_duration:
            params["minDuration"] = min_duration
        if max_duration:
            params["maxDuration"] = max_duration
        payload = self._get(
            self.TRACES_UID,
            "/api/traces",
            params=params,
        )
        return {
            "traces": payload.get("data", []),
            "metrics": {},
            "query_window": {"start": start, "end": end},
        }

    def probe(self) -> dict[str, Any]:
        """Return compact smoke-test evidence for bounded discovery and Jaeger access."""
        services = self.query_service_names()
        trace_service = (
            "frontend-proxy"
            if "frontend-proxy" in services
            else (services[0] if services else "")
        )
        traces = self.query_traces(trace_service, limit=1) if trace_service else {"traces": []}
        historical_bounds = {"lookback_minutes": 1440}
        historical_metrics = self.query_timeseries(
            "count(up)",
            time_bounds=historical_bounds,
        )
        historical_start, historical_end, _start_seconds, _end_seconds = (
            self._query_window(historical_bounds)
        )
        return {
            "query_window": self.query_window,
            "service_count": len(services),
            "trace_service": trace_service,
            "trace_count": len(traces.get("traces", [])),
            "historical_query_window": {
                "start": historical_start,
                "end": historical_end,
            },
            "historical_metric_series_count": len(
                historical_metrics.get("data", {}).get("result", [])
            ),
        }

    @property
    def query_window(self) -> dict[str, str]:
        return {"start": self._start, "end": self._end}

    @property
    def allowed_query_window(self) -> dict[str, str]:
        return {"start": self._allowed_start, "end": self._allowed_end}

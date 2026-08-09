"""Bounded read-only telemetry backend for the public ORCA environment."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from core.domain.types.retrieval import TimeBounds


def _unix_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


_RELATIVE_TIME_RE = re.compile(r"^-(?P<amount>\d+)(?P<unit>[mhd])$")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
        session: requests.Session | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._auth = (username, password)
        self._verify = verify_ssl
        self._start = start_time
        self._end = end_time
        self._start_seconds = _unix_seconds(start_time)
        self._end_seconds = _unix_seconds(end_time)
        self._session = session or requests.Session()

    def _query_window(
        self,
        time_bounds: dict[str, Any] | TimeBounds | None,
    ) -> tuple[str, str, float, float]:
        """Resolve OpenSRE's native time controls against ORCA's simulated clock."""
        if time_bounds is None:
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

        current = datetime.fromtimestamp(self._end_seconds, tz=UTC)
        if end > current:
            raise ValueError(
                "Telemetry time bounds cannot extend past ORCA's simulated current time"
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
        effective_limit = max(1, limit)
        body = {
            "size": effective_limit,
            "sort": [{"@timestamp": "asc"}],
            "query": {"bool": {"filter": filters}},
        }
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
        }

    def query_service_names(self) -> list[str]:
        """Return service names represented in bounded OpenSearch log documents."""
        body = {
            "size": 0,
            "query": {
                "range": {"@timestamp": {"gte": self._start, "lte": self._end}}
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
        **_: Any,
    ) -> dict[str, Any]:
        """Query bounded Jaeger traces for a concrete service."""
        if not service_name:
            return {"traces": [], "metrics": {}, "query_window": self.query_window}
        start, end, start_seconds, end_seconds = self._query_window(time_bounds)
        effective_limit = max(1, limit)
        payload = self._get(
            self.TRACES_UID,
            "/api/traces",
            params={
                "service": service_name,
                "start": int(start_seconds * 1_000_000),
                "end": int(end_seconds * 1_000_000),
                "limit": effective_limit,
            },
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

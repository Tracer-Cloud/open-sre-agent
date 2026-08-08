"""Bounded read-only telemetry backend for the public ORCA environment."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests


def _unix_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


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

    def query_timeseries(self, query: str = "", **_: Any) -> dict[str, Any]:
        """Execute a bounded Prometheus range query at the task's simulated time."""
        return self._get(
            self.METRICS_UID,
            "/api/v1/query_range",
            params={
                "query": query,
                "start": self._start_seconds,
                "end": self._end_seconds,
                "step": 60,
            },
        )

    def query_logs(
        self,
        service_name: str = "",
        *,
        limit: int = 20,
        **_: Any,
    ) -> dict[str, Any]:
        """Search bounded OpenSearch log documents and return a Loki-compatible shape."""
        if not service_name:
            return {
                "status": "success",
                "data": {"resultType": "streams", "result": []},
            }
        filters: list[dict[str, Any]] = [
            {"range": {"@timestamp": {"gte": self._start, "lte": self._end}}}
        ]
        filters.append({"term": {"resource.service.name.keyword": service_name}})
        effective_limit = min(max(1, limit), 20)
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
        values: list[tuple[str, str, str]] = []
        for hit in hits:
            source = hit.get("_source", {})
            timestamp = str(source.get("@timestamp", ""))
            timestamp_ns = str(int(_unix_seconds(timestamp) * 1_000_000_000))
            message = source.get("body") or source.get("message") or json.dumps(source)
            actual_service = source.get("resource.service.name") or service_name
            values.append([timestamp_ns, str(message), str(actual_service)])
        streams: dict[str, list[list[str]]] = {}
        for timestamp_ns, message, actual_service in values:
            streams.setdefault(actual_service, []).append([timestamp_ns, message])
        return {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {"stream": {"service_name": name}, "values": stream_values}
                    for name, stream_values in streams.items()
                ],
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
    ) -> list[dict[str, Any]]:
        """Query Grafana annotations using the same explicit task window."""
        params: list[tuple[str, Any]] = [
            ("from", int(self._start_seconds * 1000)),
            ("to", int(self._end_seconds * 1000)),
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
        **_: Any,
    ) -> dict[str, Any]:
        """Query bounded Jaeger traces for a concrete service."""
        if not service_name:
            return {"traces": [], "metrics": {}, "query_window": self.query_window}
        effective_limit = min(max(1, limit), 5)
        payload = self._get(
            self.TRACES_UID,
            "/api/traces",
            params={
                "service": service_name,
                "start": int(self._start_seconds * 1_000_000),
                "end": int(self._end_seconds * 1_000_000),
                "limit": effective_limit,
            },
        )
        return {
            "traces": payload.get("data", []),
            "metrics": {},
            "query_window": self.query_window,
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
        return {
            "query_window": self.query_window,
            "service_count": len(services),
            "trace_service": trace_service,
            "trace_count": len(traces.get("traces", [])),
        }

    @property
    def query_window(self) -> dict[str, str]:
        return {"start": self._start, "end": self._end}

"""Synthetic RCA scenario using Grafana Tempo as the evidence source.

Validates that a Tempo alert seeds the correct tools and that a mock backend
returns realistic fixture trace data through the single action-based tool.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.synthetic

from app.core.domain.alerts.alert_source import ALERT_SOURCE_TO_SEED_TOOL_SOURCES
from app.tools.TempoTool import query_tempo


class _FixtureTempoBackend:
    """Minimal fixture backend for synthetic Tempo scenarios."""

    def search_traces(self, **kwargs: Any) -> dict[str, Any]:
        service = kwargs.get("service") or "checkout-service"
        return {
            "source": "tempo",
            "action": "search",
            "available": True,
            "query": '{ resource.service.name = "checkout-service" }',
            "total": 2,
            "traces": [
                {
                    "trace_id": "trace-001",
                    "root_service_name": service,
                    "root_trace_name": "POST /checkout",
                    "start_time_unix_nano": "1716120000000000000",
                    "duration_ms": 2400,
                    "matched_spans": 8,
                },
                {
                    "trace_id": "trace-002",
                    "root_service_name": service,
                    "root_trace_name": "POST /checkout",
                    "start_time_unix_nano": "1716120030000000000",
                    "duration_ms": 2100,
                    "matched_spans": 7,
                },
            ],
        }

    def get_trace_by_id(self, trace_id: str) -> dict[str, Any]:
        return {
            "source": "tempo",
            "action": "get_trace",
            "available": True,
            "trace_id": trace_id,
            "total_spans": 2,
            "spans": [
                {
                    "name": "POST /checkout",
                    "span_id": "span-1",
                    "parent_span_id": "",
                    "service_name": "checkout-service",
                    "duration_ms": 2400.0,
                    "status_code": 2,
                    "status_message": "upstream timeout",
                    "attributes": {"http.status_code": "504"},
                },
                {
                    "name": "GET /inventory",
                    "span_id": "span-2",
                    "parent_span_id": "span-1",
                    "service_name": "inventory-service",
                    "duration_ms": 2300.0,
                    "status_code": 2,
                    "status_message": "deadline exceeded",
                    "attributes": {"rpc.grpc.status_code": "4"},
                },
            ],
        }

    def list_services(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "tempo",
            "action": "list_services",
            "available": True,
            "total": 2,
            "services": ["checkout-service", "inventory-service"],
        }

    def list_span_names(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "tempo",
            "action": "list_span_names",
            "available": True,
            "total": 2,
            "span_names": ["POST /checkout", "GET /inventory"],
        }


def test_tempo_alert_source_maps_to_tools() -> None:
    """Tempo alert source seeds tempo tools before the ReAct loop."""
    assert "tempo" in ALERT_SOURCE_TO_SEED_TOOL_SOURCES
    assert ALERT_SOURCE_TO_SEED_TOOL_SOURCES["tempo"] == ("tempo",)


def test_tempo_search_synthetic_scenario() -> None:
    backend = _FixtureTempoBackend()
    result = query_tempo(service="checkout-service", tempo_backend=backend)
    assert result["available"] is True
    assert result["total"] == 2
    assert result["traces"][0]["duration_ms"] == 2400


def test_tempo_get_trace_synthetic_scenario() -> None:
    backend = _FixtureTempoBackend()
    result = query_tempo(action="get_trace", trace_id="trace-001", tempo_backend=backend)
    assert result["available"] is True
    assert result["total_spans"] == 2
    assert any(s["service_name"] == "inventory-service" for s in result["spans"])
    assert result["spans"][0]["attributes"]["http.status_code"] == "504"


def test_tempo_list_services_synthetic_scenario() -> None:
    backend = _FixtureTempoBackend()
    result = query_tempo(action="list_services", tempo_backend=backend)
    assert result["available"] is True
    assert "checkout-service" in result["services"]

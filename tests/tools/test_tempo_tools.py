"""Tests for the Grafana Tempo tool."""

from __future__ import annotations

from typing import Any

from app.tools.TempoTool import _tempo_is_available, query_tempo


class _FakeTempoBackend:
    """Fake Tempo backend for tool dispatch tests."""

    def get_trace_by_id(self, trace_id: str) -> dict[str, Any]:
        return {
            "source": "tempo",
            "action": "get_trace",
            "available": True,
            "trace_id": trace_id,
            "total_spans": 1,
            "spans": [{"name": "GET /x", "service_name": "api"}],
        }

    def search_traces(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "tempo",
            "action": "search",
            "available": True,
            "total": 1,
            "traces": [{"trace_id": "t1", "root_service_name": kwargs.get("service") or "api"}],
        }

    def list_services(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "tempo",
            "action": "list_services",
            "available": True,
            "total": 2,
            "services": ["api", "worker"],
        }

    def list_span_names(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "tempo",
            "action": "list_span_names",
            "available": True,
            "total": 1,
            "span_names": ["GET /x"],
        }


class TestTempoAvailability:
    def test_available_with_connection_verified_and_url(self) -> None:
        assert (
            _tempo_is_available(
                {"tempo": {"url": "http://localhost:3200", "connection_verified": True}}
            )
            is True
        )

    def test_unavailable_with_url_only_no_connection_verified(self) -> None:
        assert _tempo_is_available({"tempo": {"url": "http://localhost:3200"}}) is False

    def test_available_with_backend(self) -> None:
        assert _tempo_is_available({"tempo": {"_backend": object()}}) is True

    def test_unavailable_when_empty(self) -> None:
        assert _tempo_is_available({}) is False


class TestTempoToolDispatch:
    def test_search_default_action(self) -> None:
        result = query_tempo(service="api", tempo_backend=_FakeTempoBackend())
        assert result["action"] == "search"
        assert result["traces"][0]["root_service_name"] == "api"

    def test_get_trace_action(self) -> None:
        result = query_tempo(
            action="get_trace", trace_id="trace-1", tempo_backend=_FakeTempoBackend()
        )
        assert result["action"] == "get_trace"
        assert result["trace_id"] == "trace-1"
        assert result["spans"][0]["service_name"] == "api"

    def test_list_services_action(self) -> None:
        result = query_tempo(action="list_services", tempo_backend=_FakeTempoBackend())
        assert result["services"] == ["api", "worker"]

    def test_list_span_names_action(self) -> None:
        result = query_tempo(action="list_span_names", tempo_backend=_FakeTempoBackend())
        assert result["span_names"] == ["GET /x"]

    def test_invalid_action_falls_back_to_search(self) -> None:
        result = query_tempo(action="bogus", tempo_backend=_FakeTempoBackend())
        assert result["action"] == "search"

    def test_not_configured_without_backend(self) -> None:
        result = query_tempo(action="search")
        assert result["available"] is False
        assert "not configured" in result["error"].lower()

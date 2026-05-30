"""Unit tests for the Grafana Tempo mixin."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import httpx

from app.services.grafana.tempo import TempoMixin


class FakeGrafanaClient(TempoMixin):
    """Fake Grafana client to test the TempoMixin."""

    def __init__(self, is_configured: bool = True):
        self.is_configured = is_configured
        self.account_id = "test-account-123"
        self.tempo_datasource_uid = "tempo-uid-abc"
        self._client = MagicMock()

    def _build_datasource_url(self, uid: str, path: str) -> str:
        return f"https://grafana.fake/api/datasources/uid/{uid}{path}"

    def _make_request(self, url: str, params: dict | None = None) -> dict:
        del url, params
        return {}

    def _get_client(self) -> MagicMock:
        return self._client


class TestTempoMixin:
    """Tests for the Tempo trace query capabilities."""

    def test_query_tempo_not_configured(self):
        """Test behavior when the client is not configured."""
        client = FakeGrafanaClient(is_configured=False)
        result = client.query_tempo(service_name="auth-service")

        assert result["success"] is False
        assert "not configured for account" in result["error"]
        assert result["traces"] == []

    def test_query_tempo_general_exception(self):
        """Test general exception handling during a query."""
        client = FakeGrafanaClient()
        client._make_request = Mock(side_effect=Exception("Connection timeout"))

        result = client.query_tempo(service_name="auth-service")

        assert result["success"] is False
        assert result["error"] == "Connection timeout"
        assert result["traces"] == []

    def test_query_tempo_http_exception_with_response(self):
        """Test exception handling with HTTPStatusError."""
        client = FakeGrafanaClient()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Permission denied for this datasource"

        err = httpx.HTTPStatusError(
            "HTTP Error",
            request=MagicMock(),
            response=mock_response,
        )
        client._make_request = Mock(side_effect=err)

        result = client.query_tempo(service_name="auth-service")

        assert result["success"] is False
        assert result["error"] == "Tempo query failed: 403"
        assert "Permission denied" in result["response"]

    def test_query_tempo_successful_trace_parsing(self):
        """Test a successful trace query and the subsequent span parsing."""
        client = FakeGrafanaClient()

        # Mock the search response (from self._make_request)
        client._make_request = Mock(
            return_value={
                "traces": [
                    {
                        "traceID": "trace-123",
                        "rootServiceName": "auth-service",
                        "durationMs": 150,
                        "spanCount": 2,
                    }
                ]
            }
        )

        # Mock the trace details response (from _get_client().get in _get_trace_details)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "batches": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "name": "DB Query",
                                    "attributes": [
                                        {
                                            "key": "db.system",
                                            "value": {"stringValue": "postgresql"},
                                        },
                                        {"key": "http.status_code", "value": {"intValue": 200}},
                                    ],
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        client._client.get.return_value = mock_response

        # Execute
        result = client.query_tempo(service_name="auth-service")

        # Assert Search result parsing
        assert result["success"] is True
        assert result["total_traces"] == 1
        assert result["service_name"] == "auth-service"
        assert len(result["traces"]) == 1

        # Assert enriched trace structure
        enriched_trace = result["traces"][0]
        assert enriched_trace["trace_id"] == "trace-123"
        assert enriched_trace["root_service"] == "auth-service"
        assert enriched_trace["duration_ms"] == 150
        assert enriched_trace["span_count"] == 2

        # Assert span parsing and attribute extraction
        assert len(enriched_trace["spans"]) == 1
        span = enriched_trace["spans"][0]
        assert span["name"] == "DB Query"
        assert span["attributes"]["db.system"] == "postgresql"
        assert span["attributes"]["http.status_code"] == 200

    def test_get_trace_details_network_failure(self):
        """Test _get_trace_details graceful degradation on network error."""
        client = FakeGrafanaClient()
        client._client.get.side_effect = Exception("Requests connection error")

        result = client._get_trace_details(trace_id="trace-123")

        # Should catch the error and return empty spans gracefully
        assert result == {"spans": []}

    def test_extract_span_attributes_edge_cases(self):
        """Test extraction of various attribute types."""
        client = FakeGrafanaClient()

        mock_span = {
            "attributes": [
                {"key": "valid_string", "value": {"stringValue": "test"}},
                {"key": "valid_int", "value": {"intValue": 42}},
                {"key": "unsupported_type", "value": {"boolValue": True}},
                {"key": "empty_value", "value": {}},
                {"value": {"stringValue": "missing_key"}},  # Should be skipped!
            ]
        }

        attributes = client._extract_span_attributes(mock_span)

        assert attributes.get("valid_string") == "test"
        assert attributes.get("valid_int") == 42
        assert "unsupported_type" not in attributes
        assert "empty_value" not in attributes
        assert "" not in attributes

    def test_get_trace_details_non_200_status(self):
        """Test _get_trace_details when the API returns a non-200 status."""
        client = FakeGrafanaClient()

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=mock_response,
        )
        client._client.get.return_value = mock_response

        result = client._get_trace_details(trace_id="trace-123")

        # Assert it safely falls back to empty spans
        assert result == {"spans": []}

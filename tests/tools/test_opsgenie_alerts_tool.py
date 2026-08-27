from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.opsgenie.tools import OpsGenieAlertsTool, _map_opsgenie_alerts


def _tool() -> OpsGenieAlertsTool:
    return OpsGenieAlertsTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"opsgenie": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"opsgenie": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "opsgenie": {
            "api_key": "key-1",
            "region": "eu",
            "query": "status=open",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "key-1"
    assert params["region"] == "eu"
    assert params["query"] == "status=open"


@patch("integrations.opsgenie.tools.make_opsgenie_client")
def test_run_returns_alerts_and_open_subset(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {
        "success": True,
        "alerts": [
            {"id": "a1", "status": "open", "priority": "P1"},
            {"id": "a2", "status": "closed", "priority": "P3"},
        ],
        "total": 2,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", region="us", query="")
    assert result["available"] is True
    assert result["total"] == 2
    assert len(result["open_alerts"]) == 1
    assert result["open_alerts"][0]["id"] == "a1"


@patch("integrations.opsgenie.tools.make_opsgenie_client")
def test_run_empty_alerts_list(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {"success": True, "alerts": [], "total": 0}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["alerts"] == []
    assert result["open_alerts"] == []


@patch("integrations.opsgenie.tools.make_opsgenie_client")
def test_run_returns_unavailable_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {"success": False, "error": "HTTP 403"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is False
    assert "403" in result["error"]


@patch("integrations.opsgenie.tools.make_opsgenie_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="")
    assert result["available"] is False


@patch("integrations.opsgenie.tools.make_opsgenie_client")
def test_run_passes_query_and_limit(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {"success": True, "alerts": [], "total": 0}
    mock_make.return_value = mock_client

    _tool().run(api_key="k", query="status=open", limit=5)
    mock_client.list_alerts.assert_called_once_with(query="status=open", limit=5)


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "opsgenie_alerts"
    assert t.source == "opsgenie"
    assert "api_key" in t.input_schema["required"]


class TestMapOpsgenieAlerts:
    def test_records_entry_with_open_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_opsgenie_alerts(
            evidence,
            {
                "available": True,
                "total": 2,
                "alerts": [{"status": "open"}, {"status": "closed"}],
                "open_alerts": [{"status": "open"}],
            },
            {"limit": 20},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "opsgenie_alerts"
        assert entries[0]["summary"] == "2 alert(s), 1 open"

    def test_records_zero_open_count_as_a_genuine_finding(self) -> None:
        """Regression: alerts is non-empty here, so "0 open" is a meaningful
        finding to cite, not noise to suppress."""
        evidence: dict[str, Any] = {}

        _map_opsgenie_alerts(
            evidence,
            {"available": True, "total": 1, "alerts": [{"status": "closed"}], "open_alerts": []},
            {"limit": 20},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 alert(s), 0 open"

    def test_qualifies_both_counts_when_page_is_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_opsgenie_alerts(
            evidence,
            {
                "available": True,
                "total": 100,
                "alerts": [{"status": "open"}] * 100,
                "open_alerts": [{"status": "open"}] * 100,
            },
            {"limit": 100},
        )

        assert evidence["catalog_entries"][0]["summary"] == "100+ alert(s), 100+ open"

    def test_qualifies_zero_open_when_page_is_saturated(self) -> None:
        """Regression: a saturated page with zero open alerts visible must
        not claim '0 open' as an exact total -- there could be more beyond
        the returned page."""
        evidence: dict[str, Any] = {}

        _map_opsgenie_alerts(
            evidence,
            {
                "available": True,
                "total": 100,
                "alerts": [{"status": "closed"}] * 100,
                "open_alerts": [],
            },
            {"limit": 100},
        )

        assert evidence["catalog_entries"][0]["summary"] == "100+ alert(s), 0+ open"

    def test_records_nothing_when_no_alerts(self) -> None:
        evidence: dict[str, Any] = {}

        _map_opsgenie_alerts(
            evidence, {"available": True, "total": 0, "alerts": [], "open_alerts": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_opsgenie_alerts(evidence, {"available": False, "error": "HTTP 403"}, {})

        assert "catalog_entries" not in evidence

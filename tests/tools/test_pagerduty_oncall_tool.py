"""Unit tests for PagerDutyOnCallTool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.pagerduty_tools import PagerDutyOnCallTool


def _tool() -> PagerDutyOnCallTool:
    return PagerDutyOnCallTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "pagerduty": {
            "api_key": "pd-key",
            "base_url": "https://api.pagerduty.com",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "pd-key"
    assert params["escalation_policy_ids"] == []


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_returns_oncalls(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_oncalls.return_value = {
        "success": True,
        "oncalls": [
            {
                "user": {"id": "U1", "summary": "Alice", "type": "user_reference"},
                "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
                "escalation_level": 1,
                "schedule": {"id": "S1", "summary": "Primary", "type": "schedule_ref"},
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-02T00:00:00Z",
            },
        ],
        "total": 1,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["total"] == 1
    assert result["oncalls"][0]["user"]["summary"] == "Alice"


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_empty_oncalls(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_oncalls.return_value = {"success": True, "oncalls": [], "total": 0}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["oncalls"] == []
    assert result["total"] == 0


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_returns_unavailable_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_oncalls.return_value = {"success": False, "error": "HTTP 403: Forbidden"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is False
    assert "403" in result["error"]


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="")
    assert result["available"] is False


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_passes_escalation_policy_ids(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_oncalls.return_value = {"success": True, "oncalls": [], "total": 0}
    mock_make.return_value = mock_client

    _tool().run(api_key="k", escalation_policy_ids=["EP1", "EP2"], limit=10)
    mock_client.get_oncalls.assert_called_once_with(
        escalation_policy_ids=["EP1", "EP2"],
        limit=10,
    )


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "pagerduty_oncall"
    assert t.source == "pagerduty"
    assert "api_key" in t.input_schema["required"]

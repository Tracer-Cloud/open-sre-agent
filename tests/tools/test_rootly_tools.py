from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from integrations.rootly.tools.alerts import rootly_alerts
from integrations.rootly.tools.incidents import _extract_params as extract_incident_params
from integrations.rootly.tools.incidents import rootly_incidents
from integrations.rootly.tools.on_call import rootly_on_call
from integrations.rootly.tools.timeline import rootly_post_timeline_event
from integrations.rootly.tools.timeline_approval import TIMELINE_EVENT_APPROVAL_DISPLAY
from tools.registry import get_registered_tools

_ROOTLY_TOOLS = {
    "rootly_alerts",
    "rootly_incidents",
    "rootly_on_call",
    "rootly_post_timeline_event",
}
_READ_ONLY_ROOTLY_TOOLS = _ROOTLY_TOOLS - {"rootly_post_timeline_event"}


def _client() -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    return client


def _patch_client(monkeypatch: pytest.MonkeyPatch, module: str, client: MagicMock) -> None:
    monkeypatch.setattr(f"integrations.rootly.tools.{module}.resolve_client", lambda *_a: client)


def test_rootly_tools_are_on_the_action_surface() -> None:
    """The whole point of the integration: a Slack turn must be able to call these.

    Tools default to the investigation surface only, where a chat turn cannot
    reach them.
    """
    action_tools = {tool.name for tool in get_registered_tools("action")}

    assert action_tools >= _ROOTLY_TOOLS


def test_the_write_tool_is_approval_gated() -> None:
    """An un-gated write would post to a live incident timeline without asking."""
    registered = {tool.name: tool for tool in get_registered_tools("action")}
    write = registered["rootly_post_timeline_event"]

    assert write.requires_approval is True
    assert write.side_effect_level == "mutating"
    assert write.approval_display is not None


@pytest.mark.parametrize("name", sorted(_READ_ONLY_ROOTLY_TOOLS))
def test_the_read_tools_cannot_mutate_rootly(name: str) -> None:
    """Only the timeline write may carry a side effect.

    ``rootly_alerts`` is the one most likely to grow one: acknowledging or
    resolving is defensible behind approval, but paging is not, and both are
    deliberately absent. A tool that gains a write path silently would reach a
    live pager.
    """
    registered = {tool.name: tool for tool in get_registered_tools("action")}

    assert registered[name].requires_approval is False
    assert registered[name].side_effect_level != "mutating"


@pytest.mark.parametrize("name", sorted(_ROOTLY_TOOLS))
def test_connection_details_cannot_be_overridden_by_the_model(name: str) -> None:
    """Base URL is protected with the token: an attacker-chosen host would receive it."""
    registered = {tool.name: tool for tool in get_registered_tools("action")}

    assert registered[name].injected_params == (
        "rootly_token",
        "rootly_base_url",
        "rootly_timeout",
    )


def test_extract_params_prefers_get_when_an_incident_is_in_context() -> None:
    params = extract_incident_params(
        {
            "rootly": {
                "api_token": "secret",
                "base_url": "https://api.rootly.com",
                "incident_id": "42",
            }
        }
    )

    assert params["action"] == "get"
    assert params["incident_id"] == "42"
    assert params["rootly_token"] == "secret"


def test_extract_params_falls_back_to_list_without_an_incident() -> None:
    params = extract_incident_params({"rootly": {"api_token": "secret"}})

    assert params["action"] == "list"


def test_events_action_reads_the_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    client.list_incident_events.return_value = {"success": True, "events": []}
    _patch_client(monkeypatch, "incidents", client)

    result = rootly_incidents(action="events", incident_id="42", limit=5, rootly_token="secret")

    assert result["action"] == "events"
    client.list_incident_events.assert_called_once_with("42", page_size=5)


def test_unknown_action_degrades_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hallucinated action must not become a silent no-op."""
    client = _client()
    client.list_incidents.return_value = {"success": True, "incidents": []}
    _patch_client(monkeypatch, "incidents", client)

    result = rootly_incidents(action="resolve", rootly_token="secret")

    assert result["action"] == "list"
    client.list_incidents.assert_called_once()


def test_get_without_an_incident_id_never_calls_rootly(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    _patch_client(monkeypatch, "incidents", client)

    result = rootly_incidents(action="get", rootly_token="secret")

    assert result["available"] is False
    assert "incident_id" in result["error"]
    client.get_incident.assert_not_called()


def test_client_failure_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    client.list_incidents.return_value = {"success": False, "error": "HTTP 403: nope"}
    _patch_client(monkeypatch, "incidents", client)

    result = rootly_incidents(rootly_token="secret")

    assert result["available"] is False
    assert result["incidents"] == []


def test_alerts_get_without_an_alert_id_never_calls_rootly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    _patch_client(monkeypatch, "alerts", client)

    result = rootly_alerts(action="get", rootly_token="secret")

    assert result["available"] is False
    assert "alert_id" in result["error"]
    client.get_alert.assert_not_called()


def test_unknown_alerts_action_degrades_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hallucinated write action must read, not silently no-op."""
    client = _client()
    client.list_alerts.return_value = {"success": True, "alerts": []}
    _patch_client(monkeypatch, "alerts", client)

    result = rootly_alerts(action="acknowledge", rootly_token="secret")

    assert result["action"] == "list"
    client.list_alerts.assert_called_once()
    client.get_alert.assert_not_called()


def test_timeline_write_normalizes_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised visibility must stay internal, never publish to customers."""
    client = _client()
    client.post_timeline_event.return_value = {"success": True, "event_id": "ev-1"}
    _patch_client(monkeypatch, "timeline", client)

    rootly_post_timeline_event(
        incident_id=" 42 ",
        event="Root cause: bad deploy.",
        visibility="Externel",
        rootly_token="secret",
    )

    client.post_timeline_event.assert_called_once_with(
        "42",
        event="Root cause: bad deploy.",
        visibility="internal",
    )


def test_approval_prompt_leads_with_external_visibility() -> None:
    arguments: dict[str, Any] = {
        "incident_id": "42",
        "event": "Publishing the mitigation.",
        "visibility": "external",
    }

    details = TIMELINE_EVENT_APPROVAL_DISPLAY.details(arguments)

    assert details.splitlines()[0].startswith("external")
    assert "customer-facing" in details
    assert "Publishing the mitigation." in details
    assert "42" in TIMELINE_EVENT_APPROVAL_DISPLAY.headline(arguments)


def test_approval_prompt_says_internal_by_default() -> None:
    details = TIMELINE_EVENT_APPROVAL_DISPLAY.details({"incident_id": "42", "event": "Note."})

    assert details.splitlines()[0].startswith("internal")


def test_receipt_is_empty_until_rootly_assigns_an_id() -> None:
    arguments = {"incident_id": "42", "event": "Note."}

    assert TIMELINE_EVENT_APPROVAL_DISPLAY.receipt(arguments, {}) == ""
    assert "ev-1" in TIMELINE_EVENT_APPROVAL_DISPLAY.receipt(
        arguments, {"event_id": "ev-1", "visibility": "internal"}
    )


def test_on_call_entitlement_gap_leaves_the_incident_tools_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-tool isolation: on-call entitlement gap does not affect incidents."""

    # Mock on-call tool to return entitlement gap
    def mock_on_call_client(*_a: Any) -> MagicMock:
        client = _client()
        client.list_on_call.return_value = {
            "success": False,
            "entitled": False,
            "error": "Rootly On-Call returned HTTP 403. Account lacks on-call permissions.",
        }
        return client

    # Mock incidents tool to return success
    def mock_incidents_client(*_a: Any) -> MagicMock:
        client = _client()
        client.list_incidents.return_value = {"success": True, "incidents": []}
        return client

    monkeypatch.setattr("integrations.rootly.tools.on_call.resolve_client", mock_on_call_client)
    monkeypatch.setattr("integrations.rootly.tools.incidents.resolve_client", mock_incidents_client)

    # On-call should be unavailable due to entitlement gap
    on_call_result = rootly_on_call(rootly_token="secret")
    assert on_call_result["available"] is False
    assert on_call_result.get("entitled") is False

    # But incidents should still work
    incidents_result = rootly_incidents(rootly_token="secret")
    assert incidents_result["available"] is True

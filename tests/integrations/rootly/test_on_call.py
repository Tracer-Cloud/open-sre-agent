"""Tests for on-call specific response shaping and PII protection."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from integrations.config_models import RootlyIntegrationConfig
from integrations.rootly.client import RootlyClient
from integrations.rootly.tools.on_call import rootly_on_call

_TOKEN = "rootly-secret-token"

_USER_WITH_CONTACT_DETAILS = {
    "full_name": "Jane Doe",
    "full_name_with_team": "Jane Doe (Platform)",
    "time_zone": "Europe/London",
    "email": "jane@example.invalid",
    "phone_number": "+1-555-1234",
    "phone": "+1-555-1234",
    "phone_numbers": ["+1-555-1234", "+1-555-5678"],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
}

_ON_CALL_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "id": "12345",
            "type": "on_call_resources",
            "attributes": {
                "user_id": 678,
                "escalation_policy_name": "Platform Primary",
                "escalation_policy_id": "ep-1",
                "escalation_level": 1,
                "escalation_policy_path_name": "Default",
                "schedule_name": "Platform Weekly",
                "schedule_id": "sch-1",
                "notification_type": "audible",
                "starts_at": "2026-08-07T09:00:00Z",
                "ends_at": "2026-08-14T09:00:00Z",
            },
        }
    ],
    "included": [{"id": "678", "type": "users", "attributes": _USER_WITH_CONTACT_DETAILS}],
}


@pytest.fixture
def client() -> RootlyClient:
    return RootlyClient(RootlyIntegrationConfig(api_token=_TOKEN))


def _http_error(status: int, text: str) -> httpx.HTTPStatusError:
    response = MagicMock()
    response.status_code = status
    response.text = text
    return httpx.HTTPStatusError("boom", request=MagicMock(), response=response)


def test_contact_details_never_reach_the_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Required PII pin, asserted on the whole tool result, not just the shaper.

    Serializing the tool's return value means a leak anywhere in the chain --
    shaper, client envelope, or tool envelope -- fails this test.
    """

    def fake_get(_path: str, _params: dict[str, Any]) -> dict[str, Any]:
        return _ON_CALL_PAYLOAD

    def fake_resolve_client(*_args: Any) -> RootlyClient:
        built = RootlyClient(RootlyIntegrationConfig(api_token=_TOKEN))
        monkeypatch.setattr(built, "_get", fake_get)
        return built

    monkeypatch.setattr("integrations.rootly.tools.on_call.resolve_client", fake_resolve_client)

    result = rootly_on_call(action="who_is_on_call", rootly_token=_TOKEN)

    serialized = json.dumps(result)
    for leaked in (
        "jane@example.invalid",
        "+1-555-1234",
        "+1-555-5678",
        "phone_number",
        "phone",
        "phone_numbers",
        "email",
        "created_at",
        "updated_at",
    ):
        assert leaked not in serialized, f"{leaked!r} reached the tool result"

    # The useful half still arrives.
    person = result["on_call"][0]["person"]
    assert person["name"] == "Jane Doe"
    assert person["time_zone"] == "Europe/London"


def test_who_is_on_call_includes_users_and_joins_the_integer_user_id(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify path, include parameter, and int->str user_id join."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, params))
        return {
            "data": [
                {
                    "id": "12345",
                    "type": "on_call_resources",
                    "attributes": {
                        "user_id": 678,
                        "escalation_policy_name": "Platform Primary",
                        "schedule_name": "Platform Weekly",
                        "notification_type": "audible",
                    },
                }
            ],
            "included": [
                {
                    "id": "678",
                    "type": "users",
                    "attributes": {
                        "full_name": "Jane Doe",
                        "full_name_with_team": "Jane Doe (Platform)",
                        "time_zone": "Europe/London",
                    },
                }
            ],
        }

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.list_on_call(include_users=True)

    # Verify correct API call
    assert calls[0][0] == "/v1/oncalls"
    assert calls[0][1]["include"] == "user"

    # Verify user_id join: int 678 should resolve against str "678"
    assert result["success"] is True
    assert result["on_call"][0]["person"]["name"] == "Jane Doe"


def test_who_is_on_call_never_sends_a_page_size(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/v1/oncalls has no paging; limit is applied client-side."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, params))
        # Return 3 rows to test client-side truncation
        return {
            "data": [
                {"id": f"{i}", "type": "on_call_resources", "attributes": {"user_id": i}}
                for i in range(3)
            ]
        }

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.list_on_call(limit=1)

    # No page[size] parameter should be sent
    assert "page[size]" not in calls[0][1]

    # But client-side limit should apply
    assert result["returned"] == 1
    assert result["truncated"] is True
    # total reports what Rootly returned, not the size of the slice we kept.
    assert result["total"] == 3


def test_forbidden_on_call_is_an_entitlement_gap_not_a_service_error(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 on on-call should not generate Sentry events - it's a product gap."""
    captured_events: list[Any] = []

    def fake_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        raise _http_error(HTTPStatus.FORBIDDEN, "access denied")

    def fake_capture_service_error(*args: Any, **kwargs: Any) -> None:
        captured_events.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr(
        "integrations.rootly.client.capture_service_error", fake_capture_service_error
    )

    result = client.list_on_call()

    assert result["entitled"] is False
    assert "On-Call" in result["error"]
    assert _TOKEN not in result["error"]
    # Most importantly: no Sentry events were captured
    assert len(captured_events) == 0


def test_unauthorized_is_not_reported_as_an_entitlement_gap(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401 should use generic error path, not entitlement degradation."""
    captured_events: list[Any] = []

    def fake_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        raise _http_error(HTTPStatus.UNAUTHORIZED, "bad token")

    def fake_capture_service_error(*args: Any, **kwargs: Any) -> None:
        captured_events.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr(
        "integrations.rootly.client.capture_service_error", fake_capture_service_error
    )

    result = client.list_on_call()

    # Should NOT be reported as an entitlement gap
    assert result.get("entitled") is not False  # None or missing, not explicitly False
    assert str(HTTPStatus.UNAUTHORIZED.value) in result["error"]
    # Should have captured one Sentry event (generic error path)
    assert len(captured_events) == 1


def test_a_non_standard_status_code_does_not_crash_the_error_mapper(
    client: RootlyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rootly sits behind Cloudflare, which emits 5xx codes outside HTTPStatus.

    Resolving the code through ``HTTPStatus(...)`` raises ValueError, which would
    escape the on-call error handler and take down the whole tool call.
    """

    def fake_get(_path: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise _http_error(520, "cloudflare: web server returned an unknown error")

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr("integrations.rootly.client.capture_service_error", lambda *_a, **_k: None)

    result = client.list_on_call()

    assert result["success"] is False
    # Not an entitlement gap, and not an exception.
    assert result.get("entitled") is not False
    assert _TOKEN not in result["error"]

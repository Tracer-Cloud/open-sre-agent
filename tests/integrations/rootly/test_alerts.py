"""Tests for alert response shaping, PII protection, and error mapping.

These drive a real ``httpx`` transport rather than monkeypatching ``_get``.
That matters for the status-code cases: a fabricated ``HTTPStatusError`` built
around a ``MagicMock`` response cannot show that ``raise_for_status`` and the
error mapper agree on a status Cloudflare invented. It also means the PII
assertions cover the whole chain — client, shapers, tool envelope — rather than
the shaper alone. The on-call review found a shaper-scoped PII test that stayed
green while a payload injected one layer lower leaked every address.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.rootly.client import RootlyClient, make_rootly_client
from integrations.rootly.tools.alerts import rootly_alerts

_TOKEN = "rootly-secret-token"
_BASE_URL = "https://api.rootly.example"

# Planted in the hostile payload below. None may appear in a tool result.
_EMAIL = "oncall.person@example.invalid"
_SECOND_EMAIL = "notified.person@example.invalid"
_PROFILELESS_EMAIL = "no.profile@example.invalid"
_NAMELESS_EMAIL = "no.name.at.all@example.invalid"
_SERVICE_NOTIFY_EMAIL = "service-owners@example.invalid"
_ENVIRONMENT_NOTIFY_EMAIL = "environment-owners@example.invalid"
_FUNCTIONALITY_NOTIFY_EMAIL = "functionality-owners@example.invalid"
_PHONE = "+1-555-1234"
_SLACK_ID = "U0PRIVATE1"
_PAYLOAD_SECRET = "hunter2-not-a-real-password"

_LEAKABLE = (
    _EMAIL,
    _SECOND_EMAIL,
    _PROFILELESS_EMAIL,
    _NAMELESS_EMAIL,
    _SERVICE_NOTIFY_EMAIL,
    _ENVIRONMENT_NOTIFY_EMAIL,
    _FUNCTIONALITY_NOTIFY_EMAIL,
    _PHONE,
    _SLACK_ID,
    _PAYLOAD_SECRET,
)


def _hostile_alert() -> dict[str, Any]:
    """One alert carrying every PII vector Rootly's schema allows.

    ``responders`` is ``UserFlatResponse`` (required ``email``, plus ``phone``
    and ``slack_id``); ``notified_users`` is ``User`` (required ``email``); and
    ``Service`` carries ``notify_emails`` — a third vector the person allowlist
    never sees, because a service is not a person. ``data`` is free-form:
    whatever the upstream monitor POSTed.

    ``unexpected_contact_field`` stands in for a field Rootly adds next
    quarter. A denylist would pass it through; a structural allowlist cannot.
    """
    return {
        "id": "alert-1",
        "type": "alerts",
        "attributes": {
            "short_id": "ALT-7",
            "summary": "CPU saturation on checkout",
            "status": "triggered",
            "source": "datadog",
            "noise": "not_noise",
            "alert_urgency": {"name": "high"},
            "started_at": "2026-08-07T10:00:00Z",
            "url": "https://rootly.example/alerts/alert-1",
            "description": "Sustained CPU above 95% for ten minutes.",
            "services": [
                {
                    "name": "checkout",
                    "notify_emails": [_SERVICE_NOTIFY_EMAIL],
                    "slack_channels": [{"id": "C123"}],
                }
            ],
            "groups": [{"name": "payments-team", "notify_emails": [_SERVICE_NOTIFY_EMAIL]}],
            "environments": [{"name": "production", "notify_emails": [_ENVIRONMENT_NOTIFY_EMAIL]}],
            "functionalities": [
                {"name": "card-payments", "notify_emails": [_FUNCTIONALITY_NOTIFY_EMAIL]}
            ],
            "labels": [{"key": "region", "value": "us-central1"}],
            "responders": [
                {
                    "id": 4242,
                    "full_name": "Jane Doe",
                    "full_name_with_team": "Jane Doe (Platform)",
                    "email": _EMAIL,
                    "phone": _PHONE,
                    "slack_id": _SLACK_ID,
                    "time_zone": "Europe/London",
                    "unexpected_contact_field": _PHONE,
                },
                # Rootly leaves ``full_name`` null on users who never completed
                # a profile. The obvious fallback is the one field that must
                # never be emitted.
                {
                    "id": 77,
                    "full_name": None,
                    "first_name": "Unnamed",
                    "last_name": "Profile",
                    "email": _PROFILELESS_EMAIL,
                    "time_zone": "UTC",
                },
                # And a user with no name of any kind, where the only string
                # left that identifies them is the one being withheld.
                {"id": 78, "full_name": None, "email": _NAMELESS_EMAIL, "time_zone": "UTC"},
            ],
            "notified_users": [
                {"id": 99, "full_name": "Sam Rivers", "email": _SECOND_EMAIL, "time_zone": "UTC"}
            ],
            "alerting_targets": [
                {"id": "ep-1", "type": "escalation_policies"},
                # A target can be a person, and then it carries their contact
                # detail inline rather than through ``responders``.
                {"id": 4242, "type": "users", "email": _EMAIL, "phone": _PHONE},
            ],
            "data": {
                "monitor_id": 55,
                "db_password": _PAYLOAD_SECRET,
                "reporter_email": _EMAIL,
            },
        },
    }


def _client_over(handler: Any) -> RootlyClient:
    """A real ``RootlyClient`` whose only fake part is the network."""
    client = make_rootly_client(_TOKEN, base_url=_BASE_URL)
    assert client is not None
    client._client = httpx.Client(
        base_url=client.config.base_url,
        headers=client.config.headers,
        transport=httpx.MockTransport(handler),
    )
    return client


def _serving(payload: dict[str, Any], *, status: int = 200) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, client: RootlyClient) -> None:
    monkeypatch.setattr("integrations.rootly.tools.alerts.resolve_client", lambda *_a: client)


def _silence_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.rootly.client.capture_service_error", lambda *_a, **_kw: None)


def test_contact_details_never_reach_the_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Required PII pin, asserted on the whole tool result over a real transport."""
    _patch_resolve(monkeypatch, _client_over(_serving({"data": _hostile_alert()})))

    result = rootly_alerts(action="get", alert_id="alert-1", rootly_token=_TOKEN)

    assert result["available"] is True
    serialized = json.dumps(result)
    for leaked in _LEAKABLE:
        assert leaked not in serialized, f"{leaked!r} reached the tool result"

    # The useful half still arrives, and only the four allowed keys with it.
    responder = result["alert"]["responders"][0]
    assert set(responder) == {"id", "name", "name_with_team", "time_zone"}
    assert responder["name"] == "Jane Doe"


def test_contact_details_never_reach_a_list_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """``list`` is the default action, so it is the path most turns take.

    The single-record shape is the one that carries responders, which makes it
    the obvious place to look for a leak and the wrong place to stop: the list
    shape still embeds services, groups and environments, all of which carry
    ``notify_emails``. Deleting the shaper from ``list_alerts`` left every
    get-scoped assertion green.
    """
    payload = {"data": [_hostile_alert()], "meta": {"total_count": 1}}
    _patch_resolve(monkeypatch, _client_over(_serving(payload)))

    result = rootly_alerts(rootly_token=_TOKEN)

    assert result["available"] is True
    assert result["alerts"], "the list path returned nothing to assert on"
    serialized = json.dumps(result)
    for leaked in _LEAKABLE:
        assert leaked not in serialized, f"{leaked!r} reached a list result"


def test_a_person_without_a_profile_name_is_not_named_by_their_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The natural fallback for a null ``full_name`` is the withheld field.

    Rootly leaves ``full_name`` null on users who never completed a profile,
    and ``email`` is required on every user model — so an email-shaped fallback
    would look correct in every fixture that sets a name and leak on every real
    account that has one of these users. Both fallback rungs are pinned: the
    last one is where the temptation is strongest, because by then the email is
    the only string left that identifies the person at all.
    """
    _patch_resolve(monkeypatch, _client_over(_serving({"data": _hostile_alert()})))

    responders = rootly_alerts(action="get", alert_id="alert-1", rootly_token=_TOKEN)["alert"][
        "responders"
    ]

    assert responders[1]["name"] == "Unnamed Profile"
    assert responders[2]["name"] == "Unknown"


def test_the_upstream_payload_is_reduced_to_field_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names tell the model more exists; values are whatever a webhook sent.

    Truncating this blob would not help — a truncated credential is still a
    credential — so the values are dropped outright.
    """
    _patch_resolve(monkeypatch, _client_over(_serving({"data": _hostile_alert()})))

    alert = rootly_alerts(action="get", alert_id="alert-1", rootly_token=_TOKEN)["alert"]

    assert alert["payload_fields"] == ["db_password", "monitor_id", "reporter_email"]
    assert "data" not in alert


def test_nested_service_objects_are_reduced_to_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """``notify_emails`` rides on four different nested lists, not just services.

    Each is a PII vector the person allowlist never sees, because none of them
    is a person. ``alerting_targets`` is the fourth: a target can *be* a user,
    and then it carries contact detail inline, so it is reduced to id and type.
    """
    _patch_resolve(monkeypatch, _client_over(_serving({"data": _hostile_alert()})))

    alert = rootly_alerts(action="get", alert_id="alert-1", rootly_token=_TOKEN)["alert"]

    assert alert["services"] == ["checkout"]
    assert alert["groups"] == ["payments-team"]
    assert alert["environments"] == ["production"]
    assert alert["functionalities"] == ["card-payments"]
    assert alert["labels"] == {"region": "us-central1"}
    assert alert["alerting_targets"] == [
        {"id": "ep-1", "type": "escalation_policies"},
        {"id": "4242", "type": "users"},
    ]


def test_forbidden_alerts_is_an_entitlement_gap_not_a_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403 means the account lacks Alerts, and would otherwise page Sentry per turn.

    ``capture_service_error`` classifies 403 as ``severity="error"``, so an
    unentitled account would file one error on every chat turn, forever.
    """
    captured: list[Any] = []
    monkeypatch.setattr(
        "integrations.rootly.client.capture_service_error",
        lambda exc, **_kw: captured.append(exc),
    )
    _patch_resolve(monkeypatch, _client_over(_serving({"errors": []}, status=403)))

    result = rootly_alerts(rootly_token=_TOKEN)

    assert result["available"] is False
    assert result["entitled"] is False
    assert captured == []


def test_a_bad_token_is_not_an_entitlement_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 and 403 must not collapse into one message.

    They point somewhere different: 401 means re-run setup, 403 means the
    account does not have Alerts. Reporting an expired token as a missing
    product sends an operator off to buy what they already own, and it also
    silences the telemetry that would show a whole deployment's token expiring.
    """
    captured: list[Any] = []
    monkeypatch.setattr(
        "integrations.rootly.client.capture_service_error",
        lambda exc, **_kw: captured.append(exc),
    )
    _patch_resolve(
        monkeypatch, _client_over(_serving({"errors": []}, status=HTTPStatus.UNAUTHORIZED))
    )

    result = rootly_alerts(rootly_token=_TOKEN)

    assert result["available"] is False
    assert result.get("entitled") is not False
    assert str(HTTPStatus.UNAUTHORIZED.value) in result["error"]
    assert len(captured) == 1


def test_a_missing_alert_is_not_an_entitlement_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 on one alert is a wrong id far more often than a missing product.

    It degrades on ``list`` — an unmounted route — but must not on ``get``, or
    a typo sends an operator to go buy something they already own.
    """
    _silence_telemetry(monkeypatch)
    _patch_resolve(monkeypatch, _client_over(_serving({"errors": []}, status=404)))

    result = rootly_alerts(action="get", alert_id="nope", rootly_token=_TOKEN)

    assert result["available"] is False
    assert result.get("entitled") is not False


def test_a_non_standard_status_code_does_not_crash_the_error_mapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloudflare fronts Rootly and emits 520/521/524.

    ``HTTPStatus(520)`` raises ``ValueError``, which would escape the error
    handler and surface as an unhandled exception rather than a tool result.
    """
    _silence_telemetry(monkeypatch)
    _patch_resolve(monkeypatch, _client_over(_serving({"errors": []}, status=520)))

    result = rootly_alerts(rootly_token=_TOKEN)

    assert result["available"] is False
    assert result["alerts"] == []


def test_truncation_reports_rootlys_count_not_the_page_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alerts page server-side, unlike ``/v1/oncalls`` where ``total`` is local."""
    payload = {"data": [_hostile_alert()], "meta": {"total_count": 137}}
    _patch_resolve(monkeypatch, _client_over(_serving(payload)))

    result = rootly_alerts(rootly_token=_TOKEN)

    assert result["returned"] == 1
    assert result["total"] == 137
    assert result["truncated"] is True


def test_monitor_supplied_text_is_bounded_on_the_list_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``summary`` is written by the upstream monitor and rides 50 to a page.

    A stack trace or a rendered dashboard pasted into one alert headline is a
    nuisance; fifty of them is the whole context window, and the model has no
    way to ask for less.
    """
    alert = _hostile_alert()
    alert["attributes"]["summary"] = "x" * 10_000
    _patch_resolve(monkeypatch, _client_over(_serving({"data": [alert]})))

    summary = rootly_alerts(rootly_token=_TOKEN)["alerts"][0]["summary"]

    assert len(summary) < 1_000


def test_list_filters_reach_rootly_as_jsonapi_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rootly has no ``filter[urgency]`` and no ``filter[search]`` on alerts.

    Pins the filter names actually sent, so a rename cannot silently return the
    unfiltered firehose while the tool still reports success.
    """
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"data": []})

    _patch_resolve(monkeypatch, _client_over(handler))

    rootly_alerts(
        status="triggered",
        alert_source="datadog",
        service="checkout",
        environment="production",
        started_after="2026-08-01T00:00:00Z",
        limit=5,
        rootly_token=_TOKEN,
    )

    assert seen["filter[status]"] == "triggered"
    assert seen["filter[source]"] == "datadog"
    assert seen["filter[services]"] == "checkout"
    assert seen["filter[environments]"] == "production"
    assert seen["filter[started_at][gte]"] == "2026-08-01T00:00:00Z"
    assert seen["page[size]"] == "5"


def test_an_oversized_limit_is_capped_and_alerts_come_back_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two things the model cannot see and would otherwise get wrong.

    A model asking for "all alerts" picks a large number, and an uncapped
    ``page[size]`` puts hundreds of alerts into one prompt. And a page of the
    *oldest* alerts is worse than no page at all during an incident — the
    explicit sort is what makes the truncation safe to reason about.
    """
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"data": []})

    _patch_resolve(monkeypatch, _client_over(handler))

    rootly_alerts(limit=5000, rootly_token=_TOKEN)

    assert seen["page[size]"] == "50"
    assert seen["sort"] == "-created_at"

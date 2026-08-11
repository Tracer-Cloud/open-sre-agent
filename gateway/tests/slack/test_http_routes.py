"""Route wiring: each Slack path uses the admission built for its payload shape.

Admission is unit-tested elsewhere. These tests exist because the two routes
once shared one handler, so every form-encoded button click was rejected by the
JSON events path — a defect invisible to admission-level tests.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from gateway.core.runtime.approvals import ApprovalBroker
from gateway.transports.slack.connection.http_receiver import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    InMemorySlackEventDeduplicator,
)
from gateway.transports.slack.connection.http_server import (
    EVENTS_PATH,
    INTERACTIVITY_PATH,
    build_slack_http_app,
)
from gateway.transports.slack.connection.signature import expected_signature
from gateway.transports.slack.settings import SlackGatewaySettings, SlackInboundTransport

_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
_TIMESTAMP = str(int(__import__("time").time()))


def _settings() -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        signing_secret=_SECRET,
        inbound_transport=SlackInboundTransport.EVENTS_API_HTTP,
        allowed_user_ids=["U1"],
    )


def _client(submitted: list[Any]) -> TestClient:
    app = build_slack_http_app(
        settings=_settings(),
        approvals=ApprovalBroker(),
        deduplicator=InMemorySlackEventDeduplicator(),
        submit_turn=submitted.append,
    )
    return TestClient(app)


def _headers(body: bytes) -> dict[str, str]:
    return {
        SIGNATURE_HEADER: expected_signature(
            signing_secret=_SECRET, timestamp=_TIMESTAMP, body=body
        ),
        TIMESTAMP_HEADER: _TIMESTAMP,
    }


def test_interactivity_route_accepts_a_form_encoded_button_click() -> None:
    """A signed click must be accepted, not rejected as malformed JSON."""
    # Arrange.
    submitted: list[Any] = []
    click = {"type": "block_actions", "user": {"id": "U1"}, "actions": [{"action_id": "approve"}]}
    body = urlencode({"payload": json.dumps(click)}).encode()

    # Act.
    response = _client(submitted).post(INTERACTIVITY_PATH, content=body, headers=_headers(body))

    # Assert — accepted, and never routed to the turn executor.
    assert response.status_code == HTTPStatus.OK
    assert submitted == []


def test_events_route_submits_a_turn_for_a_signed_mention() -> None:
    # Arrange.
    submitted: list[Any] = []
    payload = {
        "type": "event_callback",
        "event_id": "Ev1",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "user": "U1",
            "text": "<@UBOT> status?",
            "channel": "C1",
            "channel_type": "channel",
            "ts": "1700000000.000100",
        },
    }
    body = json.dumps(payload).encode()

    # Act.
    response = _client(submitted).post(EVENTS_PATH, content=body, headers=_headers(body))

    # Assert.
    assert response.status_code == HTTPStatus.OK
    assert len(submitted) == 1


def test_unsigned_request_is_unauthorized_on_both_routes() -> None:
    # Arrange.
    submitted: list[Any] = []
    client = _client(submitted)
    bad = {SIGNATURE_HEADER: "v0=deadbeef", TIMESTAMP_HEADER: _TIMESTAMP}

    # Act / Assert.
    for path, body in (
        (EVENTS_PATH, b'{"type":"event_callback"}'),
        (INTERACTIVITY_PATH, urlencode({"payload": "{}"}).encode()),
    ):
        response = client.post(path, content=body, headers=bad)
        assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert submitted == []


def test_url_verification_returns_the_challenge_body() -> None:
    # Arrange.
    body = json.dumps({"type": "url_verification", "challenge": "c-123"}).encode()

    # Act.
    response = _client([]).post(EVENTS_PATH, content=body, headers=_headers(body))

    # Assert.
    assert response.status_code == HTTPStatus.OK
    assert response.text == "c-123"

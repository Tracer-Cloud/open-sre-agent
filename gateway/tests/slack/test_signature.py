"""Slack HTTP signature verification: only fresh, correctly signed bodies pass."""

from __future__ import annotations

from gateway.transports.slack.transport.events_api.signature import (
    MAX_TIMESTAMP_AGE_SECONDS,
    expected_signature,
    verify_slack_signature,
)

_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
_BODY = b'{"type":"event_callback","event":{"type":"app_mention"}}'
_NOW = 1_700_000_000.0
_TIMESTAMP = str(int(_NOW))


def _signed(body: bytes = _BODY, *, timestamp: str = _TIMESTAMP) -> str:
    return expected_signature(signing_secret=_SECRET, timestamp=timestamp, body=body)


def test_accepts_a_correctly_signed_recent_request() -> None:
    # Arrange / Act.
    accepted = verify_slack_signature(
        signing_secret=_SECRET,
        timestamp=_TIMESTAMP,
        signature=_signed(),
        body=_BODY,
        now=_NOW,
    )

    # Assert.
    assert accepted is True


def test_rejects_a_body_altered_after_signing() -> None:
    """The forged field must not reach a turn — this is the whole boundary."""
    # Arrange — signature covers the original body, attacker swaps the payload.
    forged = _BODY.replace(b"app_mention", b"FORGED_EVENT")

    # Act.
    accepted = verify_slack_signature(
        signing_secret=_SECRET,
        timestamp=_TIMESTAMP,
        signature=_signed(),
        body=forged,
        now=_NOW,
    )

    # Assert.
    assert accepted is False


def test_rejects_a_replayed_request_outside_the_window() -> None:
    # Arrange — a genuinely signed request, captured and resent later.
    stale = str(int(_NOW - MAX_TIMESTAMP_AGE_SECONDS - 1))

    # Act.
    accepted = verify_slack_signature(
        signing_secret=_SECRET,
        timestamp=stale,
        signature=_signed(timestamp=stale),
        body=_BODY,
        now=_NOW,
    )

    # Assert — valid HMAC is not enough on its own.
    assert accepted is False


def test_rejects_a_signature_made_with_another_secret() -> None:
    # Arrange.
    other = expected_signature(signing_secret="wrong-secret", timestamp=_TIMESTAMP, body=_BODY)

    # Act / Assert.
    assert (
        verify_slack_signature(
            signing_secret=_SECRET,
            timestamp=_TIMESTAMP,
            signature=other,
            body=_BODY,
            now=_NOW,
        )
        is False
    )


def test_rejects_missing_or_unparseable_headers() -> None:
    """Absent secret, signature, or a non-numeric timestamp all fail closed."""
    # Arrange.
    cases = (
        {"signing_secret": "", "timestamp": _TIMESTAMP, "signature": _signed()},
        {"signing_secret": _SECRET, "timestamp": _TIMESTAMP, "signature": ""},
        {"signing_secret": _SECRET, "timestamp": "", "signature": _signed()},
        {"signing_secret": _SECRET, "timestamp": "not-a-number", "signature": _signed()},
    )

    # Act / Assert.
    for case in cases:
        assert verify_slack_signature(body=_BODY, now=_NOW, **case) is False

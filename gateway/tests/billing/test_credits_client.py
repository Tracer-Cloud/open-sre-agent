from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from gateway.billing.credits_client import CreditsOutcome, consume_credits

_URL_ENV = "OPENSRE_WEBAPP_URL"
_SECRET_ENV = "AGENT_USAGE_SECRET"
_ORG_ENV = "OPENSRE_ORGANIZATION_ID"


@pytest.fixture
def metering_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env with the webapp URL + shared secret set, so metering is live."""
    monkeypatch.setenv(_URL_ENV, "https://app.opensre.test")
    monkeypatch.setenv(_SECRET_ENV, "sekrit")


def test_unconfigured_when_secret_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: URL + org present but no shared secret → metering is off.
    monkeypatch.setenv(_URL_ENV, "https://app.opensre.test")
    monkeypatch.delenv(_SECRET_ENV, raising=False)

    def fail_if_called(*_a: object, **_k: object) -> httpx.Response:
        raise AssertionError("no network call when metering is unconfigured")

    monkeypatch.setattr("gateway.billing.credits_client.httpx.post", fail_if_called)

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: classified unconfigured without touching the network.
    assert outcome is CreditsOutcome.UNCONFIGURED


@pytest.mark.usefixtures("metering_on")
def test_unconfigured_when_org_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: secret set but no organization id anywhere (deploy misconfig).
    monkeypatch.delenv(_ORG_ENV, raising=False)

    # Act
    outcome = consume_credits(organization_id="", reason="slack_turn")

    # Assert: unconfigured, not denied — callers must not read this as "out of credits".
    assert outcome is CreditsOutcome.UNCONFIGURED


@pytest.mark.usefixtures("metering_on")
def test_402_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the ledger reports a shortfall.
    monkeypatch.setattr(
        "gateway.billing.credits_client.httpx.post",
        lambda *_a, **_k: httpx.Response(
            HTTPStatus.PAYMENT_REQUIRED, json={"balance": 0, "required": 1}
        ),
    )

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: the one genuine refuse-the-user state.
    assert outcome is CreditsOutcome.DENIED


@pytest.mark.usefixtures("metering_on")
def test_2xx_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the ledger consumes a credit and returns the remaining balance.
    monkeypatch.setattr(
        "gateway.billing.credits_client.httpx.post",
        lambda *_a, **_k: httpx.Response(HTTPStatus.OK, json={"balance": 41.5, "consumed": 1}),
    )

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert
    assert outcome is CreditsOutcome.ALLOWED


@pytest.mark.usefixtures("metering_on")
def test_transport_error_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the webapp is unreachable — an our-side failure, not a user's shortfall.
    def unreachable(*_a: object, **_k: object) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("gateway.billing.credits_client.httpx.post", unreachable)

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: UNAVAILABLE so the gateway can fail open instead of blocking the user.
    assert outcome is CreditsOutcome.UNAVAILABLE


@pytest.mark.usefixtures("metering_on")
def test_5xx_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a server error from the ledger (any non-402, non-2xx status).
    monkeypatch.setattr(
        "gateway.billing.credits_client.httpx.post",
        lambda *_a, **_k: httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR, json={}),
    )

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: only 402 denies; every other error status is UNAVAILABLE (fail-open).
    assert outcome is CreditsOutcome.UNAVAILABLE


@pytest.mark.usefixtures("metering_on")
def test_request_matches_webapp_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: capture the outbound request the client builds.
    calls: list[dict[str, Any]] = []

    def capture(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return httpx.Response(HTTPStatus.OK, json={"balance": 9, "consumed": 2.5})

    monkeypatch.setattr("gateway.billing.credits_client.httpx.post", capture)

    # Act
    outcome = consume_credits(
        "org_x", amount=2.5, reason="investigation", metadata={"investigationId": "inv-1"}
    )

    # Assert: bearer secret + the webapp's body shape ("amount", never "units").
    assert outcome is CreditsOutcome.ALLOWED
    (call,) = calls
    assert call["url"] == "https://app.opensre.test/api/credits/consume"
    assert call["headers"]["Authorization"] == "Bearer sekrit"
    assert call["timeout"] == 5.0
    assert call["json"] == {
        "amount": 2.5,
        "organizationId": "org_x",
        "reason": "investigation",
        "investigationId": "inv-1",
    }


@pytest.mark.usefixtures("metering_on")
def test_metadata_cannot_override_billing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: hostile metadata tries to zero the charge and swap the org/reason.
    calls: list[dict[str, Any]] = []

    def capture(_url: str, **kwargs: Any) -> httpx.Response:
        calls.append(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"balance": 1})

    monkeypatch.setattr("gateway.billing.credits_client.httpx.post", capture)

    # Act
    consume_credits(
        organization_id="org_real",
        amount=1.0,
        reason="slack_turn",
        metadata={"amount": 0, "organizationId": "org_injected", "reason": "free_ride"},
    )

    # Assert: the injected markers never reach the ledger; core fields keep their
    # real values (a zero-credit charge to another org must be impossible).
    (sent,) = calls
    assert sent["json"]["amount"] == 1.0
    assert sent["json"]["organizationId"] == "org_real"
    assert sent["json"]["reason"] == "slack_turn"

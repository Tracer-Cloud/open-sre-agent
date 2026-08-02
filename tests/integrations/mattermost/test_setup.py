"""The Mattermost setup spec — the either/or rule and where fields persist.

Mattermost accepts a webhook URL *or* the server/token pair, but not neither —
a rule ``SetupField.required`` cannot express, so every field is optional on
the spec and :func:`integrations.mattermost.verifier.verify_mattermost`
enforces it. The rejection tests below therefore run the *real* verifier: it
short-circuits on an incomplete pair before any network call, and keeping the
check there is what makes setup and health checks agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import integrations.setup_flow as setup_flow
from integrations.mattermost.setup import MATTERMOST_SETUP

_ENV_PATH = Path("/tmp/opensre-test/.env")


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture every persistence call, leaving the verifier real."""
    captured: dict[str, Any] = {"store": [], "keyring": [], "env": []}
    monkeypatch.setattr(
        setup_flow,
        "upsert_integration",
        lambda _service, payload: captured["store"].append(payload),
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: captured["keyring"].append((key, value))
    )
    monkeypatch.setattr(
        setup_flow,
        "sync_env_values",
        lambda values, **_kw: captured["env"].append(dict(values)) or _ENV_PATH,
    )
    return captured


@pytest.fixture
def verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass verification, for the cases about persistence rather than the rule."""
    monkeypatch.setattr(
        setup_flow, "_verify", lambda _spec, _creds: (True, "Mattermost connected.")
    )


@pytest.mark.usefixtures("verified")
def test_webhook_only_setup_is_accepted(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(MATTERMOST_SETUP, {"webhook_url": "https://chat/hooks/abc"})

    assert outcome.ok is True
    assert writes["store"][0]["credentials"]["webhook_url"] == "https://chat/hooks/abc"


@pytest.mark.usefixtures("verified")
def test_token_pair_setup_is_accepted(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(
        MATTERMOST_SETUP,
        {"server_url": "https://chat.example.com", "auth_token": "tok"},
    )

    assert outcome.ok is True


def test_neither_path_is_rejected_before_any_write(writes: dict[str, Any]) -> None:
    """The real verifier rejects an empty setup, and nothing is persisted."""
    outcome = setup_flow.apply_setup(MATTERMOST_SETUP, {"default_channel": "chan-id"})

    assert outcome.ok is False
    assert writes == {"store": [], "keyring": [], "env": []}


def test_incomplete_pair_without_a_webhook_is_rejected(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(MATTERMOST_SETUP, {"server_url": "https://chat.example.com"})

    assert outcome.ok is False
    assert "auth_token" in outcome.detail
    assert writes["store"] == []


def test_token_pair_without_a_channel_is_rejected_by_the_real_verifier(
    monkeypatch: pytest.MonkeyPatch, writes: dict[str, Any]
) -> None:
    """Valid token credentials with no default_channel must not pass setup.

    Runs the real verifier (no ``verified`` bypass): every unattended
    delivery path needs a channel once token credentials are configured, so
    a channel-less token pair should be caught here, not discovered the
    first time a report/alarm/notification silently fails to deliver.
    """
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"username": "bot"}
    monkeypatch.setattr("integrations.mattermost.verifier.httpx.get", lambda *_a, **_kw: response)

    outcome = setup_flow.apply_setup(
        MATTERMOST_SETUP,
        {"server_url": "https://chat.example.com", "auth_token": "tok"},
    )

    assert outcome.ok is False
    assert "default_channel" in outcome.detail
    assert writes["store"] == []


@pytest.mark.usefixtures("verified")
def test_token_pair_persists_to_env_and_keyring_but_the_webhook_stays_store_only(
    writes: dict[str, Any],
) -> None:
    """The token must reach every tier (deploy preflight reads env); the webhook
    URL embeds its secret and stays in the store, like SLACK_WEBHOOK_URL."""
    setup_flow.apply_setup(
        MATTERMOST_SETUP,
        {
            "server_url": "https://chat.example.com",
            "auth_token": "tok",
            "default_channel": "chan-id",
        },
    )

    assert writes["keyring"] == [("MATTERMOST_AUTH_TOKEN", "tok")]
    env = writes["env"][0]
    assert env["MATTERMOST_SERVER_URL"] == "https://chat.example.com"
    assert env["MATTERMOST_DEFAULT_CHANNEL"] == "chan-id"
    # No env var is defined for the webhook, so it is never mirrored out.
    assert not any("WEBHOOK" in key for key in env)

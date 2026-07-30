"""The Rocket.Chat setup spec — the either/or rule and where fields persist.

Rocket.Chat accepts a webhook URL *or* the server/token/user-id trio, but not
neither — a rule ``SetupField.required`` cannot express, so every field is
optional on the spec and :func:`integrations.rocketchat.verifier.verify_rocketchat`
enforces it. The rejection tests below therefore run the *real* verifier: it
short-circuits on an incomplete pair before any network call, and keeping the
check there is what makes setup and health checks agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
from integrations.rocketchat.setup import ROCKETCHAT_SETUP

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
        setup_flow, "_verify", lambda _spec, _creds: (True, "Rocket.Chat connected.")
    )


@pytest.mark.usefixtures("verified")
def test_webhook_only_setup_is_accepted(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(ROCKETCHAT_SETUP, {"webhook_url": "https://chat/hook/abc"})

    assert outcome.ok is True
    assert writes["store"][0]["credentials"]["webhook_url"] == "https://chat/hook/abc"


@pytest.mark.usefixtures("verified")
def test_token_trio_setup_is_accepted(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(
        ROCKETCHAT_SETUP,
        {"server_url": "https://chat.example.com", "auth_token": "tok", "user_id": "uid"},
    )

    assert outcome.ok is True


def test_neither_path_is_rejected_before_any_write(writes: dict[str, Any]) -> None:
    """The real verifier rejects an empty setup, and nothing is persisted."""
    outcome = setup_flow.apply_setup(ROCKETCHAT_SETUP, {"default_channel": "#ops"})

    assert outcome.ok is False
    assert writes == {"store": [], "keyring": [], "env": []}


def test_incomplete_trio_without_a_webhook_is_rejected(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(
        ROCKETCHAT_SETUP, {"server_url": "https://chat.example.com", "auth_token": "tok"}
    )

    assert outcome.ok is False
    assert "auth_token or user_id" in outcome.detail
    assert writes["store"] == []


@pytest.mark.usefixtures("verified")
def test_token_trio_persists_to_env_and_keyring_but_the_webhook_stays_store_only(
    writes: dict[str, Any],
) -> None:
    """The trio must reach every tier (deploy preflight reads env); the webhook
    URL embeds its secret and stays in the store, like SLACK_WEBHOOK_URL."""
    setup_flow.apply_setup(
        ROCKETCHAT_SETUP,
        {
            "server_url": "https://chat.example.com",
            "auth_token": "tok",
            "user_id": "uid",
            "default_channel": "#incidents",
        },
    )

    assert writes["keyring"] == [("ROCKETCHAT_AUTH_TOKEN", "tok")]
    env = writes["env"][0]
    assert env["ROCKETCHAT_SERVER_URL"] == "https://chat.example.com"
    assert env["ROCKETCHAT_USER_ID"] == "uid"
    assert env["ROCKETCHAT_DEFAULT_CHANNEL"] == "#incidents"
    # No env var is defined for the webhook, so it is never mirrored out.
    assert not any("WEBHOOK" in key for key in env)

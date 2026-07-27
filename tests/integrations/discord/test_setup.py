"""The Discord setup spec — its finalize step and field persistence.

Registering the ``/investigate`` slash command can only happen once the app
exists, so the spec runs it as a ``finalize`` step after persistence. What
matters is that a completed setup triggers it, an incomplete one skips it
without erroring, and the outcome is reported rather than allowed to unwind the
save.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
from integrations.discord.setup import DISCORD_SETUP

_ENV_PATH = Path("/tmp/opensre-test/.env")


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
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
    monkeypatch.setattr(
        setup_flow, "_verify", lambda _spec, _creds: (True, "Discord authenticated.")
    )
    return captured


def test_slash_command_is_registered_when_an_application_id_is_given(
    monkeypatch: pytest.MonkeyPatch, writes: dict[str, Any]
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "integrations.discord.setup.register_investigate_command",
        lambda application_id, bot_token: (
            calls.append((application_id, bot_token)) or "/investigate slash command registered."
        ),
    )

    outcome = setup_flow.apply_setup(
        DISCORD_SETUP, {"bot_token": "tok", "application_id": "app-123"}
    )

    assert outcome.ok is True
    assert calls == [("app-123", "tok")]
    assert "/investigate slash command registered." in outcome.detail


def test_slash_command_is_skipped_without_an_application_id(
    monkeypatch: pytest.MonkeyPatch, writes: dict[str, Any]
) -> None:
    called = False

    def _register(_application_id: str, _bot_token: str) -> str:
        nonlocal called
        called = True
        return "should not run"

    monkeypatch.setattr("integrations.discord.setup.register_investigate_command", _register)

    outcome = setup_flow.apply_setup(DISCORD_SETUP, {"bot_token": "tok"})

    assert outcome.ok is True
    assert called is False
    assert outcome.detail == "Discord authenticated."


def test_bot_token_is_the_only_required_field(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(DISCORD_SETUP, {"application_id": "app-123"})

    assert outcome.ok is False
    assert "bot token" in outcome.detail.lower()
    assert writes["store"] == []


def test_bot_token_goes_to_the_keyring_and_the_rest_to_env(
    monkeypatch: pytest.MonkeyPatch, writes: dict[str, Any]
) -> None:
    monkeypatch.setattr("integrations.discord.setup.register_investigate_command", lambda *_a: "")

    setup_flow.apply_setup(
        DISCORD_SETUP,
        {
            "bot_token": "tok",
            "application_id": "app-123",
            "public_key": "pub",
            "default_channel_id": "chan-1",
        },
    )

    assert writes["keyring"] == [("DISCORD_BOT_TOKEN", "tok")]
    env = writes["env"][0]
    assert env["DISCORD_APPLICATION_ID"] == "app-123"
    # DISCORD_PUBLIC_KEY ends in _KEY but is classified non-secret, so it lands in .env.
    assert env["DISCORD_PUBLIC_KEY"] == "pub"
    assert env["DISCORD_DEFAULT_CHANNEL_ID"] == "chan-1"

"""/model set offers inline API-key entry instead of dead-ending on a missing key."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import surfaces.interactive_shell.command_registry.model.switching as switching


class _Console:
    is_terminal = True  # switch_llm_provider only prompts on an interactive console

    def __init__(self, key: str = "") -> None:
        self._key = key
        self.printed: list[str] = []

    def print(self, message: str = "") -> None:
        self.printed.append(str(message))

    def input(self, prompt: str = "", *, password: bool = False) -> str:
        _ = (prompt, password)
        return self._key


def _credential_status_sequence(monkeypatch: Any, *, configured_after: bool) -> None:
    """First check reports missing; the re-check after a save reports the result."""
    import config.llm_auth.credentials as credentials

    statuses = iter(
        [
            SimpleNamespace(configured=False, stale=False, detail=""),
            SimpleNamespace(configured=configured_after, stale=False, detail=""),
        ]
    )
    fallback = SimpleNamespace(configured=configured_after, stale=False, detail="")
    monkeypatch.setattr(credentials, "status", lambda _p: next(statuses, fallback))


def test_blank_key_cancels_without_saving(monkeypatch: Any) -> None:
    import surfaces.cli.llm_auth.service as service

    _credential_status_sequence(monkeypatch, configured_after=False)
    saves: list[Any] = []
    monkeypatch.setattr(service, "configure_api_key_provider", lambda **kw: saves.append(kw))

    console = _Console(key="")  # blank input = cancel
    assert switching.switch_llm_provider("openai", console) is False  # type: ignore[arg-type]
    assert saves == []
    assert any("cancelled" in line for line in console.printed)


def test_pasted_key_is_saved_and_switch_proceeds(monkeypatch: Any) -> None:
    import surfaces.cli.llm_auth.providers as providers
    import surfaces.cli.llm_auth.service as service
    import surfaces.cli.wizard.env_sync as env_sync

    _credential_status_sequence(monkeypatch, configured_after=True)
    monkeypatch.setattr(providers, "resolve_auth_profile", lambda _p: object())
    saves: list[Any] = []
    monkeypatch.setattr(service, "configure_api_key_provider", lambda **kw: saves.append(kw))
    monkeypatch.setattr(env_sync, "sync_provider_env", lambda **_: "/tmp/.env")
    monkeypatch.setattr(switching, "_reset_runtime_llm_caches", lambda: None)
    monkeypatch.setattr(switching, "render_models_table", lambda *_: None)
    monkeypatch.setattr(switching.repl_data, "load_llm_settings", lambda: {})

    console = _Console(key="sk-test-123")
    assert switching.switch_llm_provider("openai", console) is True  # type: ignore[arg-type]
    assert saves and saves[0]["api_key"] == "sk-test-123"

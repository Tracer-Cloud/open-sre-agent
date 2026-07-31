"""Focused onboarding uses mode-specific prompt copy for the integration chooser."""

from __future__ import annotations

from typing import Any

import pytest

from surfaces.cli.wizard import _integration_configurators as configurators
from surfaces.cli.wizard import _ui


def test_local_defaults_maps_legacy_aha_mode_to_focused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setattr(_ui, "get_store_path", lambda: tmp_path / "store.json")
    monkeypatch.setattr(
        _ui,
        "load_local_config",
        lambda _path: {"wizard": {"mode": "aha"}, "targets": {"local": {}}},
    )
    assert _ui._local_defaults()["wizard_mode"] == "focused"


def test_configure_selected_integrations_focused_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[str] = []

    monkeypatch.setattr(
        configurators._console,
        "print",
        lambda msg, **_kwargs: printed.append(str(msg)),
    )
    monkeypatch.setattr(
        configurators,
        "_choose",
        lambda *_args, **_kwargs: "skip",
    )

    configured, env_path = configurators._configure_selected_integrations(mode="focused")

    assert configured == []
    assert env_path is None
    assert any("Choose one integration" in line for line in printed)


def test_configure_selected_integrations_default_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[str] = []
    monkeypatch.setattr(
        configurators._console,
        "print",
        lambda msg, **_kwargs: printed.append(str(msg)),
    )
    monkeypatch.setattr(configurators, "_choose", lambda *_args, **_kwargs: "skip")

    configurators._configure_selected_integrations()

    assert any("come back later" in line for line in printed)
    assert not any("Choose one integration" in line for line in printed)


def test_configure_selected_integrations_runs_one_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(configurators._console, "print", lambda *_a, **_k: None)
    monkeypatch.setattr(configurators, "_step", lambda *_a, **_k: None)
    monkeypatch.setattr(configurators, "_choose", lambda *_args, **_kwargs: "datadog")
    monkeypatch.setattr(
        configurators,
        "_configure_datadog",
        lambda: calls.append("datadog") or ("datadog", "/tmp/.env"),
    )

    configured, env_path = configurators._configure_selected_integrations(mode="focused")

    assert calls == ["datadog"]
    assert configured == ["datadog"]
    assert env_path == "/tmp/.env"

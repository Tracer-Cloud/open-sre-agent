"""Tempo wizard dual-writes secrets to keyring, not .env."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from surfaces.cli.wizard.configurators import observability as obs


def test_configure_tempo_routes_secrets_to_keyring(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    prompts = iter(
        [
            "http://localhost:3200",
            "tempo-bearer",
            "tempo-user",
            "tempo-pass",
            "tenant-1",
        ]
    )
    synced_env_values: list[dict[str, str]] = []
    synced_env_secrets: list[tuple[str, str]] = []
    saved: list[tuple[str, dict]] = []

    monkeypatch.setattr(obs, "_integration_defaults", lambda _service: ({}, {}))
    monkeypatch.setattr(obs, "_prompt_value", lambda *_a, **_k: next(prompts))
    monkeypatch.setattr(
        obs,
        "validate_tempo_integration",
        lambda **_kwargs: MagicMock(ok=True, detail="ok"),
    )
    monkeypatch.setattr(obs, "_render_integration_result", lambda *_a, **_k: None)
    monkeypatch.setattr(
        obs,
        "upsert_integration",
        lambda service, payload: saved.append((service, payload)),
    )
    monkeypatch.setattr(
        obs,
        "sync_env_values",
        lambda values, **_k: synced_env_values.append(values) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(
        obs,
        "sync_env_secret",
        lambda key, value: synced_env_secrets.append((key, value)),
    )

    name, path = obs._configure_tempo()

    assert name == "Tempo"
    assert path == str(tmp_path / ".env")
    assert synced_env_secrets == [
        ("TEMPO_API_KEY", "tempo-bearer"),
        ("TEMPO_PASSWORD", "tempo-pass"),
    ]
    assert synced_env_values == [
        {
            "TEMPO_URL": "http://localhost:3200",
            "TEMPO_USERNAME": "tempo-user",
            "TEMPO_ORG_ID": "tenant-1",
        }
    ]
    assert "TEMPO_API_KEY" not in synced_env_values[0]
    assert "TEMPO_PASSWORD" not in synced_env_values[0]
    assert saved[0][0] == "tempo"
    assert saved[0][1]["credentials"]["api_key"] == "tempo-bearer"

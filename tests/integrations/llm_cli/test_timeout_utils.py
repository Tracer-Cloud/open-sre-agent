from __future__ import annotations

from integrations.llm_cli.timeout_utils import resolve_timeout_from_env


def test_resolve_timeout_from_env_defaults_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("X_TIMEOUT", raising=False)
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 120.0
    )


def test_resolve_timeout_from_env_defaults_when_invalid(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "oops")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 120.0
    )


def test_resolve_timeout_from_env_defaults_when_non_positive(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "0")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 120.0
    )


def test_resolve_timeout_from_env_clamps(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "1")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 30.0
    )

    monkeypatch.setenv("X_TIMEOUT", "9999")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 600.0
    )


def test_resolve_timeout_from_env_uses_value(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "180")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 180.0
    )

"""Tests for silo → webapp credential selection (M2M preferred, shared fallback)."""

from __future__ import annotations

import pytest

import integrations.slack.agent_auth as agent_auth
from config.constants.billing import MACHINE_SECRET_ENV, USAGE_SECRET_ENV


def test_prefers_minted_m2m_token_over_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: both credentials available.
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_x")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared-secret-marker")
    monkeypatch.setattr(agent_auth.clerk_m2m, "mint_agent_m2m_token", lambda: "mt_from_clerk")

    # Act
    token = agent_auth.agent_auth_token()

    # Assert: the org-scoped token wins; the shared secret does not leak through.
    assert token == "mt_from_clerk"
    assert "shared-secret-marker" not in token


def test_falls_back_to_shared_secret_when_mint_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: machine secret set but minting yields nothing.
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_x")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")
    monkeypatch.setattr(agent_auth.clerk_m2m, "mint_agent_m2m_token", lambda: "")

    # Act / Assert
    assert agent_auth.agent_auth_token() == "shared"


def test_falls_back_to_shared_secret_when_mint_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: minting blows up (Clerk unreachable, bad config, anything).
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_x")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")

    def _boom() -> str:
        raise RuntimeError("clerk exploded")

    monkeypatch.setattr(agent_auth.clerk_m2m, "mint_agent_m2m_token", _boom)

    # Act / Assert: a mint failure must degrade, never propagate into the turn.
    assert agent_auth.agent_auth_token() == "shared"


def test_uses_shared_secret_when_no_machine_secret_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: shared secret only. Minting must not be attempted.
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")

    def _should_not_mint() -> str:
        raise AssertionError("must not mint without a machine secret")

    monkeypatch.setattr(agent_auth.clerk_m2m, "mint_agent_m2m_token", _should_not_mint)

    # Act / Assert
    assert agent_auth.agent_auth_token() == "shared"


def test_empty_when_nothing_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: neither credential set — metering and vault stay switched off.
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    monkeypatch.delenv(USAGE_SECRET_ENV, raising=False)

    # Act / Assert
    assert agent_auth.agent_auth_token() == ""

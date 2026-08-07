"""Telegram principal resolution — silo org + per-user actor."""

from __future__ import annotations

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Principal
from gateway.transports.telegram.principal import (
    PrincipalResolutionError,
    resolve_telegram_principal,
    resolve_telegram_scope,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)


def test_configured_organization_owns_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_tg")
    assert resolve_telegram_principal() == Principal.org("org_tg")


def test_turn_is_refused_when_no_organization_is_configured() -> None:
    with pytest.raises(PrincipalResolutionError, match="no organization"):
        resolve_telegram_principal()


def test_scope_pairs_org_with_telegram_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_tg")
    scope = resolve_telegram_scope(user_id="tg-42")
    assert scope.principal == Principal.org("org_tg")
    assert scope.actor.id == "tg-42"


def test_empty_user_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_tg")
    with pytest.raises(PrincipalResolutionError, match="no user id"):
        resolve_telegram_scope(user_id="  ")

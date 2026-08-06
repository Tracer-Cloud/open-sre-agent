"""Discord principal / scope resolution."""

from __future__ import annotations

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.discord import DISCORD_SILO_GUILD_IDS_ENV
from gateway.transports.discord.events import DM_GUILD_ID
from gateway.transports.discord.principal import (
    PrincipalResolutionError,
    resolve_discord_principal,
    resolve_discord_scope,
)


def test_resolve_requires_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    with pytest.raises(PrincipalResolutionError, match="no organization"):
        resolve_discord_principal(guild_id="G1")


def test_dm_skips_guild_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_dm")
    monkeypatch.setenv(DISCORD_SILO_GUILD_IDS_ENV, "G_ONLY")
    principal = resolve_discord_principal(guild_id=DM_GUILD_ID)
    assert principal.id == "org_dm"


def test_guild_allowlist_refuses_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_g")
    monkeypatch.setenv(DISCORD_SILO_GUILD_IDS_ENV, "G_ONLY")
    with pytest.raises(PrincipalResolutionError, match="not an allowed guild"):
        resolve_discord_principal(guild_id="G_OTHER")


def test_scope_pairs_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_scope")
    monkeypatch.delenv(DISCORD_SILO_GUILD_IDS_ENV, raising=False)
    scope = resolve_discord_scope(guild_id="G1", user_id="U_ALICE")
    assert scope.principal.id == "org_scope"
    assert scope.actor is not None
    assert scope.actor.id == "U_ALICE"

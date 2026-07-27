"""Resolve the owning principal for a Slack team-install turn."""

from __future__ import annotations

from config.principal import Principal, StorageScope
from gateway.billing.credits_client import organization_id_for_silo
from gateway.storage.slack_installs import SlackInstallLookupError, get_slack_install


class PrincipalResolutionError(RuntimeError):
    """Raised when the owner of a Slack turn's data cannot be established."""


def resolve_slack_principal(*, team_id: str) -> Principal:
    """Principal for a Slack turn: the team's install org, else the silo org.

    Resolution decides whose credentials are read and who is billed, so an
    unreadable catalog raises instead of falling through to another owner.
    """
    team = (team_id or "").strip()
    if not team:
        raise PrincipalResolutionError("Slack turn carried no team id")

    try:
        install = get_slack_install(team)
    except SlackInstallLookupError as exc:
        raise PrincipalResolutionError(
            f"could not read the Slack install catalog for team {team}"
        ) from exc

    if install is not None and install.clerk_org_id.strip():
        return Principal.org(install.clerk_org_id)

    if silo_org := organization_id_for_silo():
        return Principal.org(silo_org)

    raise PrincipalResolutionError(
        f"Slack team {team} has no install record and no silo organization is configured"
    )


def resolve_slack_scope(*, team_id: str, user_id: str) -> StorageScope:
    """Owning principal and acting member for one Slack turn."""
    return StorageScope.for_slack_member(resolve_slack_principal(team_id=team_id), user_id)


__all__ = [
    "PrincipalResolutionError",
    "resolve_slack_principal",
    "resolve_slack_scope",
]

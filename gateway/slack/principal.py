"""Resolve the owning principal for a Slack team-install turn.

Slack-only. Telegram, CLI, and interactive shell must not import this module —
they stay unbound on the host home. Border regressions live in
``gateway/tests/test_storage_surface_borders.py``.
"""

from __future__ import annotations

import logging
import os

from config.constants.slack import SLACK_SILO_TEAM_IDS_ENV
from config.principal import Actor, Principal, StorageScope
from gateway.billing.credits_client import organization_id_for_silo
from gateway.slack.installs import SlackInstallLookupError, get_slack_install

logger = logging.getLogger(__name__)


class PrincipalResolutionError(RuntimeError):
    """Raised when the owner of a Slack turn's data cannot be established."""


def _silo_team_allowlist() -> frozenset[str]:
    """Team ids permitted to use the silo-org fallback, from env (may be empty)."""
    raw = os.getenv(SLACK_SILO_TEAM_IDS_ENV) or ""
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def resolve_slack_principal(*, team_id: str) -> Principal:
    """Principal for a Slack turn: the team's install org, else the silo org.

    Resolution decides whose credentials are read and who is billed, so an
    unreadable catalog raises instead of falling through to another owner.

    The silo-org fallback (for a team with no install record) is a
    credential-exposure vector on a silo that holds real org credentials: any
    workspace that installs the app would be attributed to the silo org. When
    ``OPENSRE_SILO_TEAM_IDS`` is set, the fallback is restricted to those teams
    and every other team is refused (fail-closed — the recommended prod
    posture). When it is unset the fallback stays permissive for dogfood but
    logs a warning so the exposure is visible.
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
        allowlist = _silo_team_allowlist()
        if allowlist and team not in allowlist:
            raise PrincipalResolutionError(
                f"Slack team {team} has no install record and is not an allowed "
                "silo team; refusing to attribute it to the silo organization"
            )
        if not allowlist:
            logger.warning(
                "[principal] team %s has no install record; attributing to the "
                "silo org via fallback. Set %s to restrict this on a silo holding "
                "real credentials.",
                team,
                SLACK_SILO_TEAM_IDS_ENV,
            )
        return Principal.org(silo_org)

    raise PrincipalResolutionError(
        f"Slack team {team} has no install record and no silo organization is configured"
    )


def slack_scope(principal: Principal, user_id: str) -> StorageScope:
    """Pair an organization with the Slack user acting in it."""
    return StorageScope(principal=principal, actor=Actor(id=user_id))


def resolve_slack_scope(*, team_id: str, user_id: str) -> StorageScope:
    """Owning principal and acting member for one Slack turn."""
    return slack_scope(resolve_slack_principal(team_id=team_id), user_id)


__all__ = [
    "PrincipalResolutionError",
    "resolve_slack_principal",
    "resolve_slack_scope",
    "slack_scope",
]

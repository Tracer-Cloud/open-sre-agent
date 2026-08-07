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
from gateway.core.billing.credits_client import organization_id_for_silo

logger = logging.getLogger(__name__)


class PrincipalResolutionError(RuntimeError):
    """Raised when the owner of a Slack turn's data cannot be established."""


def _silo_team_allowlist() -> frozenset[str]:
    """Team ids permitted to use the silo-org fallback, from env (may be empty)."""
    raw = os.getenv(SLACK_SILO_TEAM_IDS_ENV) or ""
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def resolve_slack_principal(*, team_id: str) -> Principal:
    """Principal for a Slack turn: the organization this deployment serves.

    There is no local install catalog. Resolution uses
    ``ORGANIZATION_ID`` and fails closed when it is missing.

    Attributing an unknown workspace to that organization is a
    credential-exposure vector on a silo that holds real org credentials: any
    workspace that installed the app would inherit them. When
    ``OPENSRE_SILO_TEAM_IDS`` is set, only those teams are served and every
    other team is refused (fail-closed — the recommended prod posture). When
    it is unset the mode stays permissive for dogfood but logs a warning so
    the exposure is visible.
    """
    team = (team_id or "").strip()
    if not team:
        raise PrincipalResolutionError("Slack turn carried no team id")

    silo_org = organization_id_for_silo()
    if not silo_org:
        raise PrincipalResolutionError(
            f"Slack team {team} cannot be resolved: no organization is configured "
            "for this deployment"
        )

    allowlist = _silo_team_allowlist()
    if not allowlist:
        logger.warning(
            "[principal] serving team %s from the configured organization without an "
            "allowlist. Set %s to restrict this on a deployment holding real credentials.",
            team,
            SLACK_SILO_TEAM_IDS_ENV,
        )
    elif team not in allowlist:
        raise PrincipalResolutionError(
            f"Slack team {team} is not an allowed team for this deployment; "
            "refusing to attribute it to the configured organization"
        )

    return Principal.org(silo_org)


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

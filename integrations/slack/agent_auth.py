"""Pick the bearer credential for silo → webapp calls.

Two credentials are supported: this silo's Clerk M2M token (minted from
``CLERK_MACHINE_SECRET_KEY``, org-scoped) and the shared ``AGENT_USAGE_SECRET``.
This module owns the choice between them so the credits client and the
integrations vault stay identical on that point.

Lives here rather than in ``config/`` because minting reaches
``clerk_m2m``: putting the choice in the constants leaf would make ``config``
depend upward on ``integrations`` and form an import cycle.
"""

from __future__ import annotations

import logging
import os

import integrations.slack.clerk_m2m as clerk_m2m
from config.constants.billing import MACHINE_SECRET_ENV, USAGE_SECRET_ENV

logger = logging.getLogger(__name__)


def _shared_secret() -> str:
    return (os.getenv(USAGE_SECRET_ENV) or "").strip()


def agent_auth_token() -> str:
    """Bearer token for silo → webapp calls.

    Prefers a cached Clerk M2M token when the machine secret is set, and falls
    back to the shared secret otherwise. Returns "" when neither is available,
    which leaves metering and the vault switched off.
    Never raises: a mint failure degrades to the fallback rather than breaking
    the turn that triggered it.
    """
    if os.getenv(MACHINE_SECRET_ENV):
        try:
            # Module attribute (not a from-import) so tests can patch the mint.
            token = clerk_m2m.mint_agent_m2m_token()
        except Exception as exc:  # noqa: BLE001 - never break a turn on auth mint
            logger.warning("[agent-auth] M2M mint raised (%s)", type(exc).__name__)
            token = ""
        if token:
            return token
    return _shared_secret()

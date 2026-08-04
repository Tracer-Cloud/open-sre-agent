"""Which organization this process serves.

Every deployment declares it the same way, as ``ORGANIZATION_ID``, which the
multi-tenant control plane injects into each silo. Credits metering, usage
attribution, Slack principal resolution and the fail-closed mount-ownership
check all read it through here rather than reaching for the env var, so there
is one answer to the question.
"""

from __future__ import annotations

import os

from config.constants.billing import ORGANIZATION_ID_ENV


def organization_id() -> str:
    """The organization this deployment serves, or "" when unbound.

    A laptop sets nothing and gets "", which leaves callers unbound rather than
    guessing an owner for a customer's data.
    """
    return (os.getenv(ORGANIZATION_ID_ENV) or "").strip()


__all__ = ["organization_id"]

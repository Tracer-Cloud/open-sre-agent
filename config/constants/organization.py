"""Which organization this process serves.

Two deployments name the same fact differently. The multi-tenant control plane
injects ``ORGANIZATION_ID`` into a Fargate silo; the EC2 Slack service sets
``OPENSRE_ORGANIZATION_ID``. Both hold the same Clerk organization id, so every
caller reads through :func:`organization_id` and works on either.

Reading one name directly is what broke: a Fargate silo sets only the bare name,
so credits metering, usage attribution and Slack principal resolution all saw an
empty string, and Slack turns were refused outright.
"""

from __future__ import annotations

import functools
import logging
import os

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.tenancy import TENANT_ORGANIZATION_ID_ENV

logger = logging.getLogger(__name__)

#: Checked in order. The prefixed name wins so a generic ``ORGANIZATION_ID``
#: belonging to some other tool cannot quietly take precedence.
_ORGANIZATION_ID_ENV_NAMES = (ORGANIZATION_ID_ENV, TENANT_ORGANIZATION_ID_ENV)


@functools.cache
def _warn_conflicting_names_once(chosen: str) -> None:
    """Warn once when the two names are set to different organizations."""
    logger.warning(
        "[organization] %s and %s name different organizations; using %s. "
        "They must hold the same id — one of them is misconfigured.",
        ORGANIZATION_ID_ENV,
        TENANT_ORGANIZATION_ID_ENV,
        chosen,
    )


def organization_id() -> str:
    """The organization this deployment serves, or "" when unbound.

    A laptop sets neither and gets "", which leaves callers unbound rather than
    guessing an owner for a customer's data.
    """
    values = [(name, (os.getenv(name) or "").strip()) for name in _ORGANIZATION_ID_ENV_NAMES]
    present = [(name, value) for name, value in values if value]
    if not present:
        return ""
    chosen = present[0][1]
    if len({value for _name, value in present}) > 1:
        _warn_conflicting_names_once(chosen)
    return chosen


__all__ = ["organization_id"]

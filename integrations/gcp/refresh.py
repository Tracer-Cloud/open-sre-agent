"""Parse the ``*_REFRESH_INTERVAL`` env vars the GCP integration honours.

Two unrelated things re-read Google state on a cadence — the project allow-list
(:mod:`integrations.gcp.project_discovery`) and GKE auto-registration
(:mod:`integrations.gcp.gke.autoregister`) — and an operator reasonably expects
``GCP_PROJECT_REFRESH_INTERVAL=0`` and ``GCP_GKE_REFRESH_INTERVAL=0`` to mean the
same thing. Parsing this in each of them would let the two drift apart one
edge case at a time.

A leaf module: it imports :mod:`config.constants.gcp` and stdlib only, so either
caller can import it without an import cycle.
"""

from __future__ import annotations

import logging
import math
import os

from config.constants.gcp import GCP_DEFAULT_REFRESH_INTERVAL_SECONDS

logger = logging.getLogger(__name__)

#: Spellings that mean "do not refresh — do the work once and stop". ``never``
#: is here because it is what an operator writes when ``0`` looks like it might
#: mean "no delay between refreshes" rather than "no refreshes".
_OFF = frozenset({"0", "false", "no", "off", "never"})

#: Refusing to go below a minute is not tuning-by-taste. The project listing is
#: consulted while building the params of *every* GCP tool call, so an interval
#: shorter than a turn puts a Cloud Resource Manager round trip in front of each
#: one — reintroducing exactly the cost the cache exists to remove, and against
#: a quota shared with the tools doing the real work.
MIN_INTERVAL_SECONDS = 60.0

#: What "off" resolves to: an expiry that no clock reading reaches, so the
#: first result is served for the life of the process. Callers that run a loop
#: instead of a cache should test with :func:`is_off`, not compare to this.
NEVER = math.inf


def is_off(interval: float) -> bool:
    """Whether ``interval`` means "once, then stop"."""
    return interval == NEVER


def refresh_interval(env_name: str) -> float:
    """Seconds between refreshes for ``env_name``, or :data:`NEVER` if disabled.

    Unset means :data:`~config.constants.gcp.GCP_DEFAULT_REFRESH_INTERVAL_SECONDS`,
    not "off": refreshing is the behaviour a deployment gets by default, and an
    operator who wants the old boot-only behaviour says so explicitly.

    An unparseable value falls back to the default rather than to "off". The
    two failure modes are not symmetric — a typo that silently disabled
    refreshing would look identical to it working, right up until someone asks
    why a project created last week is still invisible.
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return GCP_DEFAULT_REFRESH_INTERVAL_SECONDS
    if raw.casefold() in _OFF:
        return NEVER

    try:
        seconds = float(raw)
    except ValueError:
        logger.warning(
            "%s is not a number; using the default of %.0fs. Set it to 0 to disable refreshing.",
            env_name,
            GCP_DEFAULT_REFRESH_INTERVAL_SECONDS,
        )
        return GCP_DEFAULT_REFRESH_INTERVAL_SECONDS

    if seconds <= 0:
        return NEVER
    if seconds < MIN_INTERVAL_SECONDS:
        logger.warning(
            "%s=%s is below the %.0fs floor; using %.0fs.",
            env_name,
            raw,
            MIN_INTERVAL_SECONDS,
            MIN_INTERVAL_SECONDS,
        )
        return MIN_INTERVAL_SECONDS
    return seconds


__all__ = ["MIN_INTERVAL_SECONDS", "NEVER", "is_off", "refresh_interval"]

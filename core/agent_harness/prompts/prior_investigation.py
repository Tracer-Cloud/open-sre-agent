"""Prior-investigation facts shared by the gather and assistant prompts.

Leaf module: both ``gather`` and ``assistant`` import these headline facts, so
the wording stays identical between the turn's two prompts without either
module importing the other. ``turns.orchestrator`` imports the recall window so
every "answer from the prior investigation instead of gathering" decision uses
one bound.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

# Bounds tool *suppression*, not recall. The session holds one completed
# investigation and the user may ask about it at any point, so its findings stay
# in the answer prompt for the whole session. What expires is the licence to skip
# live evidence: past this age a retrospective-sounding question is likely about
# something new, so the turn gathers fresh data as well.
PRIOR_INVESTIGATION_RECALL_SECONDS = 30 * 60

STALE_PRIOR_INVESTIGATION_NOTE = (
    "(from earlier in this session — treat as background and prefer any fresh "
    "evidence below when they disagree)"
)


def prior_investigation_headline(state: Mapping[str, Any]) -> list[str]:
    """Return the alert name and root cause lines present in ``state``."""
    parts: list[str] = []
    alert_name = state.get("alert_name")
    if alert_name:
        parts.append(f"Alert: {alert_name}")
    root_cause = state.get("root_cause")
    if root_cause:
        parts.append(f"Root cause: {root_cause}")
    return parts


def is_within_recall_window(state: Mapping[str, Any] | None) -> bool:
    """True when ``state`` is recent enough to answer from without gathering.

    ``investigation_started_at`` is a :func:`time.monotonic` reading, valid only
    within the process that produced it — which holds because ``last_state`` is
    set from an investigation this session ran and is cleared by ``/new`` and
    ``/resume``. If it ever gets restored from disk, switch the stamp to wall
    time; a monotonic value from another process reads as stale here.

    A missing, non-numeric, or out-of-range value reads as stale, so callers
    fall back to gathering rather than answering from data they cannot date.
    """
    if not state:
        return False
    started_at = state.get("investigation_started_at")
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
        return False
    age_seconds = time.monotonic() - float(started_at)
    return 0.0 <= age_seconds <= PRIOR_INVESTIGATION_RECALL_SECONDS


__all__ = [
    "PRIOR_INVESTIGATION_RECALL_SECONDS",
    "is_within_recall_window",
    "prior_investigation_headline",
]

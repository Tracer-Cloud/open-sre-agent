"""Prior-investigation facts shared by the gather and assistant prompts.

Leaf module: both ``gather`` and ``assistant`` import these headline facts, so
the wording stays identical between the turn's two prompts without either
module importing the other. ``turns.orchestrator`` imports the recall window so
every "answer from the prior investigation instead of gathering" decision uses
one bound.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Any

_logger = logging.getLogger(__name__)

# Used only when the turn is *not* an explicit follow-up handoff: age softens
# incidental prior-RCA context (stale note on the answer prompt; gather prompt
# prefers fresh tools). Explicit ``follow_up:`` handoffs skip gather and keep
# the RCA as the answer regardless of this window — see
# ``is_prior_investigation_follow_up`` and ``orchestrator._gather_and_answer``.
PRIOR_INVESTIGATION_RECALL_SECONDS = 30 * 60

# Handoff-content prefix the action planner emits for a retrospective question
# about the investigation already completed in this session.
PRIOR_INVESTIGATION_HANDOFF_PREFIX = "follow_up:"

STALE_PRIOR_INVESTIGATION_NOTE = (
    "(from earlier in this session — treat as background and prefer any fresh "
    "evidence below when they disagree)"
)


def is_prior_investigation_follow_up(handoff_contents: tuple[str, ...]) -> bool:
    """True when the action planner classified this turn as a follow-up.

    An explicit tag is the planner's judgement about *which* incident the user
    means. It outranks the recall window for gather-skip and for whether the
    assistant treats the RCA as the answer vs background context.
    """
    return any(tag.startswith(PRIOR_INVESTIGATION_HANDOFF_PREFIX) for tag in handoff_contents)


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


def _summarize_evidence(evidence: Any) -> list[str]:
    if isinstance(evidence, dict):
        sample_keys = list(evidence)[:3]
        sample = {key: evidence[key] for key in sample_keys}
        return [
            f"Evidence items: {len(evidence)}",
            "".join(("Evidence keys: ", ", ".join(map(str, sample_keys)))),
            "".join(
                (
                    "Sample evidence:\n",
                    json.dumps(sample, indent=2, default=str)[:1500],
                )
            ),
        ]
    if isinstance(evidence, list):
        return [
            f"Evidence items: {len(evidence)}",
            "".join(
                (
                    "Sample evidence:\n",
                    json.dumps(evidence[:3], indent=2, default=str)[:1500],
                )
            ),
        ]
    return [
        f"Evidence type: {type(evidence).__name__}",
        f"Evidence summary:\n{str(evidence)[:1500]}",
    ]


def summarize(state: Mapping[str, Any]) -> str:
    """Produce a compact text summary of the previous investigation."""
    parts: list[str] = prior_investigation_headline(state)
    problem_md = state.get("problem_md") or ""
    if problem_md:
        parts.append(f"Problem summary:\n{problem_md[:2000]}")
    slack_message = state.get("slack_message") or ""
    if slack_message:
        parts.append(f"Report:\n{slack_message[:2000]}")
    evidence = state.get("evidence")
    if evidence:
        try:
            parts.extend(_summarize_evidence(evidence))
        except (TypeError, ValueError) as exc:
            _logger.warning("could not serialize evidence for grounding: %s", exc)
            parts.append("(evidence present but could not be serialized for grounding)")
    return "\n\n".join(parts) or "(no prior investigation details available)"


def build_block(
    state: Mapping[str, Any] | None,
    *,
    explicit_follow_up: bool = False,
) -> str:
    """Summarize the session's investigation, marking it background when incidental.

    An explicit follow-up handoff means the user asked about *this* investigation,
    so it is the answer — never downgrade it to background there, however long ago
    it ran. The note applies only when the block is unprompted context on some
    other turn, where fresh evidence should win a conflict.
    """
    if state is None:
        return ""
    summary = summarize(state)
    if explicit_follow_up or is_within_recall_window(state):
        return summary
    return f"{STALE_PRIOR_INVESTIGATION_NOTE}\n{summary}"


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
    "PRIOR_INVESTIGATION_HANDOFF_PREFIX",
    "PRIOR_INVESTIGATION_RECALL_SECONDS",
    "STALE_PRIOR_INVESTIGATION_NOTE",
    "build_block",
    "is_prior_investigation_follow_up",
    "is_within_recall_window",
    "prior_investigation_headline",
    "summarize",
]

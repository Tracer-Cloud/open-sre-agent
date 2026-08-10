"""Outer session goal — cross-turn continuation (distinct from inner ReAct Goal).

Attach via an explicit host call (:func:`attach_session_goal`) or from a
structured action-agent handoff tag ``session_goal:…``. Do not detect goals by
scanning user prose (no keyword / regex intent routing).

Checklist success criteria use ``session_goal_item:…`` handoffs; progress uses
``session_goal:done=<indices>`` in the assistant reply.

The host loop (:mod:`core.agent_harness.turns.session_goal_loop`) calls ``chat``
until the goal is achieved, cleared, cancelled, or hits ``max_outer_turns``.

Inner ``core.agent.goals.Goal`` / ``goal_review`` stay the per-turn ReAct gate.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from core.agent_harness.turns.handoff_tag_parse import find_tag_suffix

if TYPE_CHECKING:
    from core.agent_harness.turns.assistant_handoff import AssistantHandoff

# Persisted on flush as ``custom_message`` / ``custom_type`` (last write wins).
SESSION_GOAL_STATE_CUSTOM_TYPE = "session_goal_state"


class SessionGoalStatus:
    """Status names for :class:`SessionGoal`."""

    ACTIVE = "active"
    ACHIEVED = "achieved"
    CLEARED = "cleared"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


_ACHIEVED_TAG = "session_goal:achieved"
_DONE_TAG = re.compile(r"session_goal:done=([0-9,\s]+)")
# Whole-token progress tags removed before the user sees the reply.
_PROGRESS_TAG_LINE = re.compile(
    r"(?:^|\s)session_goal:(?:achieved|done=[0-9,\s]+)(?=\s|$)",
    re.MULTILINE,
)


@dataclass(slots=True)
class SessionGoal:
    """Host-scoped completion condition spanning multiple ``chat`` turns."""

    condition: str
    max_outer_turns: int = 5
    status: str = SessionGoalStatus.ACTIVE
    turns_used: int = 0
    step_count: int | None = None
    checklist: tuple[str, ...] = ()
    completed: frozenset[int] = frozenset()

    def with_status(self, status: str) -> SessionGoal:
        return replace(self, status=status)

    def record_turn(self) -> SessionGoal:
        return replace(self, turns_used=self.turns_used + 1)

    def with_completed(self, completed: frozenset[int]) -> SessionGoal:
        return replace(self, completed=completed)

    @property
    def checklist_complete(self) -> bool:
        if not self.checklist:
            return False
        return all(index in self.completed for index in range(len(self.checklist)))

    @property
    def unfinished_items(self) -> tuple[tuple[int, str], ...]:
        return tuple(
            (index, item)
            for index, item in enumerate(self.checklist)
            if index not in self.completed
        )


def _checklist_from_handoffs(handoff_contents: Sequence[str]) -> tuple[str, ...]:
    items: list[str] = []
    for raw in handoff_contents:
        item = find_tag_suffix(raw, "session_goal_item")
        if item:
            items.append(item)
    return tuple(items)


def session_goal_from_handoffs(
    handoff_contents: Sequence[str],
    *,
    condition: str = "",
) -> SessionGoal | None:
    """Build a :class:`SessionGoal` from action ``session_goal`` handoff tags.

    Accepted forms (structured; not fuzzy user-text matching). ``:`` and ``=``
    separators are both accepted (schema docs use ``=``; content tags often use
    ``:``):

    - ``session_goal:continue`` / ``session_goal=continue``
    - ``session_goal:max_turns=<n>`` / ``session_goal=max_turns=<n>;steps=<n>``
    - ``session_goal_item:<text>`` / ``session_goal_item=<text>``
    - ``session_goal:achieved`` / ``session_goal:done=…`` — progress tags, not
      attach tags.
    """
    checklist = _checklist_from_handoffs(handoff_contents)
    attach_tag: str | None = None
    for raw in handoff_contents:
        body = find_tag_suffix(raw, "session_goal")
        if body is None:
            continue
        # Progress tags use the same key; never treat them as attach.
        if body == "achieved" or body.startswith("done="):
            continue
        attach_tag = body
        break

    if attach_tag is None and not checklist:
        return None

    max_turns = 5
    step_count: int | None = None
    body = attach_tag or "continue"
    if body != "continue":
        for part in body.split(";"):
            piece = part.strip()
            if piece.startswith("max_turns="):
                try:
                    max_turns = max(1, int(piece.split("=", 1)[1].strip()))
                except ValueError:
                    continue
            elif piece.startswith("steps="):
                try:
                    step_count = max(1, int(piece.split("=", 1)[1].strip()))
                    max_turns = max(max_turns, step_count)
                except ValueError:
                    continue

    if checklist and step_count is None:
        step_count = len(checklist)
        max_turns = max(max_turns, step_count)

    goal_condition = condition.strip() or body
    if len(goal_condition) > 400:
        goal_condition = goal_condition[:397] + "..."
    return SessionGoal(
        condition=goal_condition,
        max_outer_turns=max_turns,
        status=SessionGoalStatus.ACTIVE,
        step_count=step_count,
        checklist=checklist,
    )


def session_goal_from_assistant_handoffs(
    handoffs: Sequence[AssistantHandoff],
    *,
    condition: str = "",
) -> SessionGoal | None:
    """Build a :class:`SessionGoal` from typed :class:`AssistantHandoff` fields."""
    # Reuse the tag body parser by projecting fields to clean content tags —
    # ontology fields are already validated at decode time.
    projected: list[str] = []
    for handoff in handoffs:
        if handoff.session_goal:
            projected.append(f"session_goal:{handoff.session_goal}")
        for item in handoff.session_goal_items:
            projected.append(f"session_goal_item:{item}")
    if not projected:
        return None
    return session_goal_from_handoffs(projected, condition=condition)


def attach_session_goal_from_handoffs(
    session: Any,
    handoff_contents: Sequence[str],
    *,
    condition: str = "",
    handoffs: Sequence[AssistantHandoff] = (),
) -> SessionGoal | None:
    """Attach a goal from typed handoffs (preferred) or legacy tag strings."""
    if session_goal_is_active(session):
        existing = getattr(session, "session_goal", None)
        return existing if isinstance(existing, SessionGoal) else None
    detected = None
    if handoffs:
        detected = session_goal_from_assistant_handoffs(handoffs, condition=condition)
    if detected is None:
        detected = session_goal_from_handoffs(handoff_contents, condition=condition)
    if detected is None:
        return None
    return attach_session_goal(session, detected)


def attach_session_goal(session: Any, goal: SessionGoal) -> SessionGoal:
    """Store ``goal`` on ``session`` and return it."""
    session.session_goal = goal
    return goal


def clear_session_goal(session: Any) -> None:
    session.session_goal = None


def session_goal_to_payload(goal: SessionGoal) -> dict[str, Any]:
    """JSON-ready dict for persistence / host transport."""
    return {
        "condition": goal.condition,
        "max_outer_turns": int(goal.max_outer_turns),
        "status": goal.status,
        "turns_used": int(goal.turns_used),
        "step_count": goal.step_count,
        "checklist": list(goal.checklist),
        "completed": sorted(int(index) for index in goal.completed),
    }


def session_goal_from_payload(payload: Any) -> SessionGoal | None:
    """Rebuild a :class:`SessionGoal` from :func:`session_goal_to_payload` output."""
    if not isinstance(payload, dict):
        return None
    condition = payload.get("condition")
    if not isinstance(condition, str) or not condition.strip():
        return None
    try:
        max_outer = max(1, int(payload.get("max_outer_turns", 5)))
        turns_used = max(0, int(payload.get("turns_used", 0)))
    except (TypeError, ValueError):
        return None
    step_raw = payload.get("step_count")
    step_count: int | None
    if step_raw is None:
        step_count = None
    else:
        try:
            step_count = max(1, int(step_raw))
        except (TypeError, ValueError):
            step_count = None
    checklist_raw = payload.get("checklist") or ()
    checklist = tuple(
        item.strip() for item in checklist_raw if isinstance(item, str) and item.strip()
    )
    completed_raw = payload.get("completed") or ()
    completed: set[int] = set()
    if isinstance(completed_raw, (list, tuple, set, frozenset)):
        for value in completed_raw:
            try:
                completed.add(int(value))
            except (TypeError, ValueError):
                continue
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        status = SessionGoalStatus.ACTIVE
    return SessionGoal(
        condition=condition.strip(),
        max_outer_turns=max_outer,
        status=status.strip(),
        turns_used=turns_used,
        step_count=step_count,
        checklist=checklist,
        completed=frozenset(completed),
    )


def session_goal_state_snapshot(session: Any) -> dict[str, Any]:
    """Flush payload for outer goal + L0 CTA dedupe / pending setup offer."""
    goal = getattr(session, "session_goal", None)
    offered = getattr(session, "offered_upgrade_ctas", None)
    offered_keys = (
        sorted(str(key) for key in offered)
        if isinstance(offered, (set, frozenset, list, tuple))
        else []
    )
    pending = getattr(session, "pending_integration_setup_offer", None)
    service_id = getattr(pending, "service_id", None)
    pending_payload = (
        {"service_id": service_id.strip()}
        if isinstance(service_id, str) and service_id.strip()
        else None
    )
    return {
        "session_goal": (session_goal_to_payload(goal) if isinstance(goal, SessionGoal) else None),
        "offered_upgrade_ctas": offered_keys,
        "pending_integration_setup_offer": pending_payload,
    }


def session_goal_state_is_empty(snapshot: dict[str, Any]) -> bool:
    """True when the snapshot carries no goal, no offered CTA, and no pending offer."""
    return not any(snapshot.values())


def apply_session_goal_state(session: Any, payload: Any) -> None:
    """Rehydrate outer goal / CTA state from a flush snapshot."""
    if not isinstance(payload, dict):
        return
    goal = session_goal_from_payload(payload.get("session_goal"))
    if hasattr(session, "session_goal"):
        session.session_goal = goal
    offered_raw = payload.get("offered_upgrade_ctas") or ()
    if hasattr(session, "offered_upgrade_ctas"):
        keys = {str(key) for key in offered_raw if isinstance(key, str) and key.strip()}
        session.offered_upgrade_ctas = keys
    pending_raw = payload.get("pending_integration_setup_offer")
    if hasattr(session, "pending_integration_setup_offer"):
        service_id = None
        if isinstance(pending_raw, dict):
            raw = pending_raw.get("service_id")
            if isinstance(raw, str) and raw.strip():
                service_id = raw.strip()
        if service_id is None:
            session.pending_integration_setup_offer = None
        else:
            from core.agent_harness.session.pending_offer import (
                PendingIntegrationSetupOffer,
            )

            session.pending_integration_setup_offer = PendingIntegrationSetupOffer(
                service_id=service_id
            )


def session_goal_is_active(session: Any) -> bool:
    """True when the session holds an active outer goal."""
    goal = getattr(session, "session_goal", None)
    if goal is None:
        return False
    # ``session`` is duck-typed, so the comparison is Any-typed without this.
    return bool(goal.status == SessionGoalStatus.ACTIVE)


def _done_indices_from_text(text: str) -> frozenset[int]:
    found: set[int] = set()
    for match in _DONE_TAG.finditer(text):
        for piece in match.group(1).split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                found.add(int(piece))
            except ValueError:
                continue
    return frozenset(found)


def apply_session_goal_progress(goal: SessionGoal, text: str) -> SessionGoal:
    """Merge ``session_goal:done=…`` indices from ``text`` into ``goal.completed``."""
    if not text:
        return goal
    newly = _done_indices_from_text(text)
    if not newly:
        return goal
    if goal.checklist:
        newly = frozenset(i for i in newly if 0 <= i < len(goal.checklist))
    if not newly:
        return goal
    return goal.with_completed(goal.completed | newly)


def strip_session_goal_progress_tags(text: str) -> str:
    """Remove harness progress tags from user-visible assistant text."""
    if not text:
        return text
    cleaned = _PROGRESS_TAG_LINE.sub(" ", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def default_evaluate_session_goal(
    goal: SessionGoal,
    result: Any,
    *,
    session: Any | None = None,
) -> str:
    """Evaluate completion from structured signals (not fuzzy user intent).

    Achieved when the reply emits ``session_goal:achieved``, or when every
    checklist item has been marked via ``session_goal:done=<index>``.
    """
    if session is not None and getattr(session, "pending_user_choice", None) is not None:
        return SessionGoalStatus.ACTIVE

    text = ""
    response = getattr(result, "assistant_response_text", None)
    if isinstance(response, str):
        text = response
    primary = getattr(result, "primary_response_text", None)
    if not text and isinstance(primary, str):
        text = primary

    if _ACHIEVED_TAG in text:
        return SessionGoalStatus.ACHIEVED

    # Prefer the (possibly already progress-updated) goal on the session.
    current = goal
    if session is not None:
        stored = getattr(session, "session_goal", None)
        if isinstance(stored, SessionGoal):
            current = stored
    current = apply_session_goal_progress(current, text)
    if session is not None and isinstance(getattr(session, "session_goal", None), SessionGoal):
        attach_session_goal(session, current)

    if current.checklist and current.checklist_complete:
        return SessionGoalStatus.ACHIEVED

    return SessionGoalStatus.ACTIVE


def format_session_goal_checklist(goal: SessionGoal) -> str:
    """Render a compact checklist for REPL / host progress display."""
    if not goal.checklist:
        return ""
    lines = ["Goal checklist:"]
    for index, item in enumerate(goal.checklist):
        mark = "[x]" if index in goal.completed else "[ ]"
        lines.append(f"  {mark} {index + 1}. {item}")
    return "\n".join(lines)


def continuation_nudge(goal: SessionGoal) -> str:
    """User-visible follow-up message for the next outer turn."""
    unfinished = goal.unfinished_items
    if unfinished:
        pending = "\n".join(f"  - [{index}] {item}" for index, item in unfinished)
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            "Unfinished checklist items (0-based indices):\n"
            f"{pending}\n\n"
            "Take the next unfinished item now. When you complete an item, include "
            "`session_goal:done=<index>` (comma-separate multiple). When every "
            "item is done, you may also include `session_goal:achieved`."
        )
    return (
        "[session_goal] Continue the active goal without asking whether to "
        f"continue. Goal: {goal.condition}\n\n"
        "Take the next unfinished step now. When every step is done, include the "
        "exact tag `session_goal:achieved` in your reply."
    )


__all__ = [
    "SESSION_GOAL_STATE_CUSTOM_TYPE",
    "SessionGoal",
    "SessionGoalStatus",
    "apply_session_goal_progress",
    "apply_session_goal_state",
    "attach_session_goal",
    "attach_session_goal_from_handoffs",
    "clear_session_goal",
    "continuation_nudge",
    "default_evaluate_session_goal",
    "format_session_goal_checklist",
    "session_goal_from_assistant_handoffs",
    "session_goal_from_handoffs",
    "session_goal_from_payload",
    "session_goal_is_active",
    "session_goal_state_is_empty",
    "session_goal_state_snapshot",
    "session_goal_to_payload",
    "strip_session_goal_progress_tags",
]

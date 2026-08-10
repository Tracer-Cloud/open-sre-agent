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
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from core.agent_harness.turns.handoff_tag_parse import find_tag_suffix
from platform.common.evidence_compaction import truncate_message

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


# Character budgets for goal text. The ellipsis arithmetic lives in
# ``truncate_message`` so no call site repeats ``limit - len("...")``.
# A reason is one line of the checklist render; a condition is persisted in
# full-ish for resume; the status-line condition shares a single Slack/Telegram
# timeline row with the status, turn counter, and reason.
MAX_GOAL_REASON_CHARS = 240
MAX_GOAL_CONDITION_CHARS = 400
MAX_GOAL_STATUS_LINE_CONDITION_CHARS = 60

# Outer turns a goal may run before the host stops on budget.
_DEFAULT_MAX_OUTER_TURNS = 5

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
    # Last host/evaluator reason shown in progress paint and continuation nudges.
    last_reason: str = ""
    # Wall-clock start for ``◎ /goal active`` duration (``time.time()``).
    started_at: float | None = None
    # Session token totals when the goal was attached — delta is goal spend.
    token_baseline_input: int = 0
    token_baseline_output: int = 0
    # True when attached via ``/goal set`` (host-owned). Handoff must not replace
    # it after achieve, and prose ``session_goal:achieved`` does not need tools.
    host_owned: bool = False

    def with_status(self, status: str) -> SessionGoal:
        return replace(self, status=status)

    def record_turn(self) -> SessionGoal:
        return replace(self, turns_used=self.turns_used + 1)

    def with_completed(self, completed: frozenset[int]) -> SessionGoal:
        return replace(self, completed=completed)

    def with_reason(self, reason: str) -> SessionGoal:
        text = truncate_message(reason.strip(), MAX_GOAL_REASON_CHARS)
        return replace(self, last_reason=text)

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

    @property
    def next_checklist_item(self) -> tuple[int, str] | None:
        unfinished = self.unfinished_items
        return unfinished[0] if unfinished else None


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

    - ``session_goal:continue`` — attach an outer goal
    - ``session_goal_max_turns:<n>`` — outer-turn cap (typed on the tool schema)
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

    max_turns = _DEFAULT_MAX_OUTER_TURNS
    explicit_cap = False
    step_count: int | None = None
    body = attach_tag or "continue"
    for raw in handoff_contents:
        cap = find_tag_suffix(raw, "session_goal_max_turns")
        if cap is None:
            continue
        try:
            max_turns = max(1, int(cap.strip()))
            explicit_cap = True
        except ValueError:
            continue
        break

    if checklist and step_count is None:
        step_count = len(checklist)
        # Default budget may stretch to fit a checklist; an explicit typed cap
        # is a hard ceiling (budget_exhausted if items remain).
        if not explicit_cap:
            max_turns = max(max_turns, step_count)

    goal_condition = condition.strip() or body
    goal_condition = truncate_message(goal_condition, MAX_GOAL_CONDITION_CHARS)
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
            projected.append("session_goal:continue")
            if handoff.session_goal_max_turns is not None:
                projected.append(f"session_goal_max_turns:{handoff.session_goal_max_turns}")
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
    """Attach a goal from typed handoffs (preferred) or legacy tag strings.

    Host-owned goals (``/goal set``) stay authoritative until the user clears
    or replaces them — handoff must not open a fresh default-cap loop after
    the slash goal achieves.
    """
    existing = getattr(session, "session_goal", None)
    if isinstance(existing, SessionGoal) and existing.host_owned:
        return existing
    if session_goal_is_active(session):
        return existing if isinstance(existing, SessionGoal) else None
    detected = None
    if handoffs:
        detected = session_goal_from_assistant_handoffs(handoffs, condition=condition)
    if detected is None:
        detected = session_goal_from_handoffs(handoff_contents, condition=condition)
    if detected is None:
        return None
    return attach_session_goal(session, detected)


def _session_token_totals(session: Any | None) -> tuple[int, int]:
    if session is None:
        return 0, 0
    tokens = getattr(session, "tokens", None)
    totals = getattr(tokens, "totals", None)
    if not isinstance(totals, dict):
        return 0, 0
    try:
        return int(totals.get("input", 0) or 0), int(totals.get("output", 0) or 0)
    except (TypeError, ValueError):
        return 0, 0


def mark_session_goal_started(
    goal: SessionGoal,
    *,
    now: float | None = None,
    session: Any | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> SessionGoal:
    """Stamp wall-clock start + token baselines for active-status UX."""
    if input_tokens is None or output_tokens is None:
        baseline_in, baseline_out = _session_token_totals(session)
        if input_tokens is None:
            input_tokens = baseline_in
        if output_tokens is None:
            output_tokens = baseline_out
    return replace(
        goal,
        started_at=float(time.time() if now is None else now),
        token_baseline_input=max(0, int(input_tokens)),
        token_baseline_output=max(0, int(output_tokens)),
    )


def attach_session_goal(session: Any, goal: SessionGoal) -> SessionGoal:
    """Store ``goal`` on ``session`` and return it.

    Fresh active goals get a start stamp (duration / token delta) unless the
    caller already set ``started_at`` (e.g. restore from payload).
    """
    if goal.started_at is None and goal.status == SessionGoalStatus.ACTIVE:
        goal = mark_session_goal_started(goal, session=session)
    session.session_goal = goal
    return goal


def clear_session_goal(session: Any) -> None:
    session.session_goal = None


def session_goal_elapsed_seconds(
    goal: SessionGoal,
    *,
    now: float | None = None,
) -> float | None:
    """Seconds since ``started_at``, or ``None`` when the clock was never stamped."""
    if goal.started_at is None:
        return None
    clock = time.time() if now is None else now
    return max(0.0, float(clock) - float(goal.started_at))


def session_goal_token_delta(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> int:
    """Token spend since attach (input+output), floored at zero."""
    if input_tokens is None or output_tokens is None:
        cur_in, cur_out = _session_token_totals(session)
        if input_tokens is None:
            input_tokens = cur_in
        if output_tokens is None:
            output_tokens = cur_out
    delta = (int(input_tokens) - int(goal.token_baseline_input)) + (
        int(output_tokens) - int(goal.token_baseline_output)
    )
    return max(0, delta)


def format_duration_compact(seconds: float) -> str:
    """Human duration for status lines (``45s``, ``1m 23s``, ``1h 02m``)."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_token_count_compact(count: int) -> str:
    """Compact token count (``150``, ``1.2k``, ``3.4M``)."""
    value = max(0, int(count))
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        scaled = value / 1000.0
        text = f"{scaled:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    scaled = value / 1_000_000.0
    text = f"{scaled:.1f}".rstrip("0").rstrip(".")
    return f"{text}M"


def session_goal_to_payload(goal: SessionGoal) -> dict[str, Any]:
    """JSON-ready dict for persistence / host transport."""
    payload: dict[str, Any] = {
        "condition": goal.condition,
        "max_outer_turns": int(goal.max_outer_turns),
        "status": goal.status,
        "turns_used": int(goal.turns_used),
        "step_count": goal.step_count,
        "checklist": list(goal.checklist),
        "completed": sorted(int(index) for index in goal.completed),
        "last_reason": goal.last_reason,
        "token_baseline_input": int(goal.token_baseline_input),
        "token_baseline_output": int(goal.token_baseline_output),
        "host_owned": bool(goal.host_owned),
    }
    if goal.started_at is not None:
        payload["started_at"] = float(goal.started_at)
    return payload


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
    reason_raw = payload.get("last_reason")
    last_reason = reason_raw.strip() if isinstance(reason_raw, str) else ""
    started_at: float | None = None
    started_raw = payload.get("started_at")
    if started_raw is not None:
        try:
            started_at = float(started_raw)
        except (TypeError, ValueError):
            started_at = None
    try:
        token_in = max(0, int(payload.get("token_baseline_input", 0) or 0))
        token_out = max(0, int(payload.get("token_baseline_output", 0) or 0))
    except (TypeError, ValueError):
        token_in, token_out = 0, 0
    host_owned = bool(payload.get("host_owned", False))
    return SessionGoal(
        condition=condition.strip(),
        max_outer_turns=max_outer,
        status=status.strip(),
        turns_used=turns_used,
        step_count=step_count,
        checklist=checklist,
        completed=frozenset(completed),
        last_reason=last_reason,
        started_at=started_at,
        token_baseline_input=token_in,
        token_baseline_output=token_out,
        host_owned=host_owned,
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


def should_persist_session_goal_state(
    snapshot: dict[str, Any],
    *,
    prior_records: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether flush should append ``snapshot`` as a ``session_goal_state`` record.

    Non-empty state always persists. An empty snapshot is skipped only when the
    transcript has never stored goal/CTA state — otherwise it is a tombstone so
    resume does not revive a cleared goal.
    """
    if not session_goal_state_is_empty(snapshot):
        return True
    return any(
        record.get("type") == "custom_message"
        and record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
        for record in prior_records
    )


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


def derive_session_goal_reason(goal: SessionGoal) -> str:
    """Structured reason for progress paint and continuation nudges.

    No LLM — derived from status + checklist progress so hosts stay honest and
    cheap.
    """
    if goal.status == SessionGoalStatus.ACHIEVED:
        return "goal achieved"
    if goal.status == SessionGoalStatus.BUDGET_EXHAUSTED:
        return f"outer turn budget exhausted ({goal.turns_used}/{goal.max_outer_turns})"
    if goal.status == SessionGoalStatus.CANCELLED:
        return "goal cancelled"
    if goal.status == SessionGoalStatus.CLEARED:
        return "goal cleared"
    if goal.checklist:
        done = len(goal.completed & frozenset(range(len(goal.checklist))))
        total = len(goal.checklist)
        nxt = goal.next_checklist_item
        if nxt is None:
            return f"checklist {done}/{total} done"
        _index, item = nxt
        return f"checklist {done}/{total} done — next: {item}"
    if goal.host_owned:
        return "waiting for session_goal:achieved"
    return "waiting for session_goal:achieved with tool evidence"


def refresh_session_goal_reason(goal: SessionGoal) -> SessionGoal:
    """Attach a fresh :func:`derive_session_goal_reason` on ``goal``."""
    return goal.with_reason(derive_session_goal_reason(goal))


def format_session_goal_checklist(goal: SessionGoal) -> str:
    """Backward-compatible alias for :func:`format_session_goal_progress`."""
    return format_session_goal_progress(goal)


def format_session_goal_progress(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    now: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> str:
    """Multi-line progress paint for REPL mid-loop updates and ``/goal show``."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    status = goal.status
    if status == SessionGoalStatus.ACTIVE:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = session_goal_token_delta(
            goal,
            session=session,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        token_text = format_token_count_compact(tokens)
        if reason.startswith("working"):
            headline = (
                f"◎ /goal active · working… · {duration} · "
                f"turn {goal.turns_used}/{goal.max_outer_turns} · +{token_text} tokens"
            )
        else:
            headline = (
                f"◎ /goal active · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
                f"· +{token_text} tokens"
            )
    else:
        headline = f"Session goal · {status} · turn {goal.turns_used}/{goal.max_outer_turns}"
    lines = [
        headline,
        f"  condition: {goal.condition}",
        f"  reason: {reason}",
    ]
    if not goal.checklist:
        return "\n".join(lines)

    lines.append("  Checklist:")
    next_index = goal.next_checklist_item[0] if goal.next_checklist_item else None
    for index, item in enumerate(goal.checklist):
        done = index in goal.completed
        mark = "[x]" if done else "[ ]"
        prefix = "→ " if (not done and index == next_index) else "  "
        lines.append(f"  {prefix}{mark} {index + 1}. {item}")
    return "\n".join(lines)


def format_session_goal_status_line(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    now: float | None = None,
) -> str:
    """Compact one-line status for gateway sinks (Slack/Telegram timelines)."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    condition = goal.condition.strip()
    condition = truncate_message(condition, MAX_GOAL_STATUS_LINE_CONDITION_CHARS)
    if goal.status == SessionGoalStatus.ACTIVE:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = format_token_count_compact(session_goal_token_delta(goal, session=session))
        return (
            f"◎ /goal active · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
            f"· +{tokens} tok · {condition} · {reason}"
        )
    return (
        f"Goal · {goal.status} · turn {goal.turns_used}/{goal.max_outer_turns} · "
        f"{condition} · {reason}"
    )


def continuation_nudge(goal: SessionGoal) -> str:
    """User-visible follow-up message for the next outer turn."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    reason_block = f"Last progress: {reason}\n\n"
    unfinished = goal.unfinished_items
    if unfinished:
        pending = "\n".join(f"  - [{index}] {item}" for index, item in unfinished)
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            f"{reason_block}"
            "Unfinished checklist items (0-based indices):\n"
            f"{pending}\n\n"
            "Take the next unfinished item now. When you complete an item, include "
            "`session_goal:done=<index>` (comma-separate multiple). When every "
            "item is done, you may also include `session_goal:achieved`."
        )
    if goal.host_owned:
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            f"{reason_block}"
            "Answer the condition directly. Do not run `/goal` as a tool. When the "
            "condition is met, include the exact tag `session_goal:achieved` in "
            "your reply (no further tool work required for a host-set goal)."
        )
    return (
        "[session_goal] Continue the active goal without asking whether to "
        f"continue. Goal: {goal.condition}\n\n"
        f"{reason_block}"
        "Take the next unfinished step now. When the goal is met after real tool "
        "work, include the exact tag `session_goal:achieved` in your reply. "
        "Do not emit that tag with no tool evidence — the host will ignore it."
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
    "derive_session_goal_reason",
    "format_duration_compact",
    "format_session_goal_checklist",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "format_token_count_compact",
    "mark_session_goal_started",
    "refresh_session_goal_reason",
    "session_goal_elapsed_seconds",
    "session_goal_from_assistant_handoffs",
    "session_goal_from_handoffs",
    "session_goal_from_payload",
    "session_goal_is_active",
    "session_goal_state_is_empty",
    "session_goal_state_snapshot",
    "session_goal_to_payload",
    "session_goal_token_delta",
    "should_persist_session_goal_state",
    "strip_session_goal_progress_tags",
]

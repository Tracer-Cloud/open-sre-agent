"""Outer session goal — cross-turn continuation (distinct from inner ReAct Goal).

Attach via an explicit host call (:func:`attach_session_goal`) or from a
structured action-agent handoff tag ``session_goal:…``. Do not detect goals by
scanning user prose (no keyword / regex intent routing).

Checklist success criteria use ``session_goal_item:…`` handoffs; progress uses
``session_goal:done=<indices>`` in the assistant reply.

The host loop (:mod:`core.agent_harness.turns.session_goal_loop`) calls ``chat``
until the goal is achieved, cleared, cancelled, or hits ``max_outer_turns``.

Related leaf modules (import them directly — this module must not import them):

* :mod:`session_goal_evaluate` — structured completion
* :mod:`session_goal_review` — optional LLM confirm
* :mod:`session_goal_paint` — progress paint / nudges
* :mod:`session_goal_persist` — flush / restore

Inner ``core.agent.goals.Goal`` / ``goal_review`` stay the per-turn ReAct gate.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from core.agent_harness.turns.handoff_tag_parse import find_tag_suffix
from platform.common.evidence_compaction import truncate_message

if TYPE_CHECKING:
    from core.agent_harness.turns.assistant_handoff import AssistantHandoff


class SessionGoalStatus:
    """Status names for :class:`SessionGoal`."""

    ACTIVE = "active"
    ACHIEVED = "achieved"
    CLEARED = "cleared"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


class SessionGoalReason:
    """Stable host reason strings for evaluate, paint, and LLM confirm.

    Call sites compare with ``==`` / helpers — do not invent parallel phrases.
    Never embed ``session_goal:…`` tag grammar here: painted reasons can land in
    captured reply text and must not look like progress claims.
    """

    WORKING_PREFIX = "working"
    ACHIEVED_TOOL_EVIDENCE = "achieved with tool evidence"
    ACHIEVED_HOST_SET = "achieved (host-set goal)"
    ACHIEVED_GENERIC = "goal achieved"
    CHECKLIST_COMPLETE = "checklist complete"
    WAITING_HOST_SIGNAL = "waiting for an achieved signal"
    WAITING_TOOL_EVIDENCE = "waiting for an achieved signal with tool evidence"
    WAITING_USER_CHOICE = "waiting for user choice"
    PAUSED_USER_CHOICE = "paused — waiting for your choice"
    BUDGET_EXHAUSTED = "outer turn budget exhausted"
    CANCELLED = "goal cancelled"
    CLEARED = "goal cleared"
    LLM_CONFIRM_NOT_REACHED = "LLM confirm: not reached"
    LLM_CONFIRM_UNAVAILABLE = "LLM confirm unavailable; staying active"
    NO_TOOL_EVIDENCE = "achieved tag ignored; no tool evidence yet"
    INVESTIGATION_RUNNING = "investigation running — continue after results"
    ACHIEVED_IGNORED_INVESTIGATION = "achieved tag ignored; investigation still running"

    @staticmethod
    def is_working(reason: str) -> bool:
        return reason.startswith(SessionGoalReason.WORKING_PREFIX)

    @staticmethod
    def working_outer_turn(turn: int, max_turns: int) -> str:
        return f"working — starting outer turn {turn}/{max_turns}"

    @staticmethod
    def budget_exhausted(turns_used: int, max_outer_turns: int) -> str:
        return f"{SessionGoalReason.BUDGET_EXHAUSTED} ({turns_used}/{max_outer_turns})"

    @staticmethod
    def checklist_progress(done: int, total: int, next_item: str | None = None) -> str:
        if next_item is None:
            return f"checklist {done}/{total} done"
        return f"checklist {done}/{total} done — next: {next_item}"

    @staticmethod
    def achieved_ignored_incomplete(done: int, total: int, next_item: str | None) -> str:
        next_bit = f" — next: {next_item}" if next_item else ""
        return f"achieved tag ignored; checklist {done}/{total} incomplete{next_bit}"


# Character budgets for goal text. The ellipsis arithmetic lives in
# ``truncate_message`` so no call site repeats ``limit - len("...")``.
# A reason is one line of the checklist render; a condition is persisted in
# full-ish for resume.
MAX_GOAL_REASON_CHARS = 240
MAX_GOAL_CONDITION_CHARS = 400

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
    # True when attached via ``/goal set``. While ACTIVE, handoff must not
    # replace it. Host-owned condition-only goals may achieve on the
    # ``session_goal:achieved`` tag without tool evidence (product rule for the
    # slash path — handoff goals still require tools).
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

    An **active** host-owned goal (``/goal set``) stays authoritative — handoff
    must not replace it mid-flight. Terminal host-owned goals (achieved /
    cleared / …) may be replaced so a later handoff can start fresh work.
    """
    existing = getattr(session, "session_goal", None)
    if (
        isinstance(existing, SessionGoal)
        and existing.host_owned
        and existing.status == SessionGoalStatus.ACTIVE
    ):
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
    io_totals = getattr(tokens, "io_totals", None)
    if callable(io_totals):
        try:
            inp, out = io_totals()
            return max(0, int(inp)), max(0, int(out))
        except (TypeError, ValueError):
            return 0, 0
    # Duck-type stores that expose a totals dict without TokenUsage.
    totals = getattr(tokens, "totals", None)
    if not isinstance(totals, dict):
        return 0, 0
    try:
        return max(0, int(totals.get("input", 0) or 0)), max(0, int(totals.get("output", 0) or 0))
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


__all__ = [
    "MAX_GOAL_CONDITION_CHARS",
    "MAX_GOAL_REASON_CHARS",
    "SessionGoal",
    "SessionGoalReason",
    "SessionGoalStatus",
    "apply_session_goal_progress",
    "attach_session_goal",
    "attach_session_goal_from_handoffs",
    "clear_session_goal",
    "mark_session_goal_started",
    "session_goal_elapsed_seconds",
    "session_goal_from_assistant_handoffs",
    "session_goal_from_handoffs",
    "session_goal_is_active",
    "session_goal_token_delta",
    "strip_session_goal_progress_tags",
]

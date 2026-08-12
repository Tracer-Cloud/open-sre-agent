"""The session goal arrives as typed fields, not a packed string.

``session_goal`` used to accept three spellings of one concept — ``continue``,
``max_turns=<n>``, and ``max_turns=<n>;steps=<n>``. A live planner picked
whichever it liked, so a fixture pinning one spelling failed against a
schema-legal answer. Attach is now a boolean and the cap is an integer, so
there is exactly one way to say each thing.
"""

from __future__ import annotations

from core.agent_harness.session_goal.goal import session_goal_from_handoffs
from core.agent_harness.turns.assistant_handoff import AssistantHandoff


def test_attaching_a_goal_is_a_boolean_not_a_spelling() -> None:
    """Arrange/Act: the planner just says "attach one"."""
    handoff = AssistantHandoff.from_tool_input(
        {"content": "Walk the checklist.", "session_goal": True}
    )

    assert handoff.session_goal is True
    assert handoff.session_goal_max_turns is None


def test_the_turn_cap_is_an_integer_field() -> None:
    """A cap is a number; the planner never encodes it into text."""
    handoff = AssistantHandoff.from_tool_input(
        {"content": "Five steps.", "session_goal": True, "session_goal_max_turns": 5}
    )

    assert handoff.session_goal_max_turns == 5


def test_typed_fields_build_the_goal_with_that_cap() -> None:
    """End to end: typed input reaches SessionGoal without a packed string."""
    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "Five steps.",
            "session_goal": True,
            "session_goal_max_turns": 5,
            "session_goal_items": ["one", "two", "three"],
        }
    )

    goal = session_goal_from_handoffs(handoff.to_handoff_contents(), condition="five steps")

    assert goal is not None
    assert goal.max_outer_turns == 5
    assert len(goal.checklist) == 3


def test_a_goal_is_not_attached_when_the_planner_says_no() -> None:
    """Single-turn asks must not pick up an session goal."""
    handoff = AssistantHandoff.from_tool_input({"content": "One answer.", "session_goal": False})

    assert handoff.session_goal is False
    assert session_goal_from_handoffs(handoff.to_handoff_contents(), condition="x") is None


def test_a_checklist_implies_the_goal_is_attached() -> None:
    """Items without the flag is a half-state; the model should not be able to make one.

    Observed live: the planner emitted ``session_goal_items`` for a five-step
    checklist and omitted ``session_goal``. A checklist only means anything
    under a goal, so attach is derived rather than separately required.
    """
    handoff = AssistantHandoff.from_tool_input(
        {"content": "Walk it.", "session_goal_items": ["one", "two"]}
    )

    assert handoff.session_goal is True
    assert handoff.session_goal_items == ("one", "two")


def test_metric_read_implies_the_goal_is_attached() -> None:
    """Count asks without ``session_goal`` still need the host continuation loop.

    Observed live: planner set ``evidence_kind=metric_read`` for Windows-users
    and omitted ``session_goal``. Gather got the SQL row but the answer was
    incomplete; without attach, ``run_until`` exited after one turn.
    """
    from core.agent_harness.session_goal.goal import session_goal_from_assistant_handoffs
    from core.agent_harness.turns.evidence_kind import EvidenceKind

    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "Windows users last 7 days.",
            "evidence_kind": "metric_read",
        }
    )

    assert handoff.evidence_kind == EvidenceKind.METRIC_READ
    assert handoff.session_goal is True
    goal = session_goal_from_assistant_handoffs((handoff,), condition="Windows users last 7 days")
    assert goal is not None
    assert goal.condition == "Windows users last 7 days"


def test_metric_read_explicit_false_opts_out_of_attach() -> None:
    """``session_goal=false`` wins over the metric_read default."""
    from core.agent_harness.session_goal.goal import session_goal_from_assistant_handoffs

    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "One-shot metric.",
            "evidence_kind": "metric_read",
            "session_goal": False,
        }
    )

    assert handoff.session_goal is False
    assert session_goal_from_assistant_handoffs((handoff,), condition="one-shot") is None


def test_the_typed_cap_survives_the_projection_to_a_goal() -> None:
    """A cap on the handoff must reach SessionGoal, not fall back to the default.

    ``session_goal_from_assistant_handoffs`` projects typed fields to tags; an
    unprojected cap silently becomes the default budget, so a small goal runs
    too long and a large one ends early as budget-exhausted.
    """
    from core.agent_harness.session_goal.goal import session_goal_from_assistant_handoffs

    handoff = AssistantHandoff.from_tool_input(
        {"content": "Three turns.", "session_goal": True, "session_goal_max_turns": 3}
    )

    goal = session_goal_from_assistant_handoffs((handoff,), condition="three turns")

    assert goal is not None
    assert goal.max_outer_turns == 3


def test_an_explicit_cap_is_not_raised_to_fit_a_longer_checklist() -> None:
    """A typed ``session_goal_max_turns`` is a hard budget, even under a checklist.

    Raising the cap to ``len(checklist)`` lets a planner's smaller explicit
    budget be exceeded before the loop runs. Honor the typed cap; the outer
    loop stops on ``budget_exhausted`` when items remain.
    """
    from core.agent_harness.session_goal.goal import session_goal_from_assistant_handoffs

    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "Five items, three turns.",
            "session_goal": True,
            "session_goal_max_turns": 3,
            "session_goal_items": ["one", "two", "three", "four", "five"],
        }
    )

    goal = session_goal_from_assistant_handoffs((handoff,), condition="five items")

    assert goal is not None
    assert goal.max_outer_turns == 3
    assert len(goal.checklist) == 5


def test_flushing_a_goalless_session_adds_no_in_memory_record() -> None:
    """The in-memory backend must not append empty goal state either.

    ``jsonl`` was fixed; the in-memory store had the same ``hasattr`` guard, so
    an empty synthetic record still became the transcript's last entry and
    re-parented the leaf.
    """
    from core.agent_harness.session.persistence.memory import InMemorySessionStore
    from core.agent_harness.session.session_core import SessionCore
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE

    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    session.record("cli_agent", "hello", ok=True)
    storage.flush(session)

    records = storage.read(session.session_id)
    goal_records = [
        record for record in records if record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert goal_records == []


def test_an_explicit_cap_is_not_raised_by_a_longer_checklist() -> None:
    """A planner that caps at 2 means 2, even with five checklist items.

    The checklist raises the *default* budget so a five-step goal is not cut
    short. It must not raise a cap the planner set deliberately, or an explicit
    limit silently becomes advisory.
    """
    from core.agent_harness.session_goal.goal import session_goal_from_handoffs

    goal = session_goal_from_handoffs(
        (
            "session_goal:continue",
            "session_goal_max_turns:2",
            "session_goal_item:one",
            "session_goal_item:two",
            "session_goal_item:three",
            "session_goal_item:four",
            "session_goal_item:five",
        ),
        condition="capped at two",
    )

    assert goal is not None
    assert goal.max_outer_turns == 2
    assert goal.step_count == 5


def test_a_checklist_still_raises_the_default_budget() -> None:
    """Without an explicit cap, a long checklist must not exhaust the default."""
    from core.agent_harness.session_goal.goal import session_goal_from_handoffs

    goal = session_goal_from_handoffs(
        tuple(["session_goal:continue"] + [f"session_goal_item:{n}" for n in range(1, 8)]),
        condition="seven steps",
    )

    assert goal is not None
    assert goal.max_outer_turns == 7

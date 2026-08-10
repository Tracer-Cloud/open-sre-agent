"""The outer goal arrives as typed fields, not a packed string.

``session_goal`` used to accept three spellings of one concept — ``continue``,
``max_turns=<n>``, and ``max_turns=<n>;steps=<n>``. A live planner picked
whichever it liked, so a fixture pinning one spelling failed against a
schema-legal answer. Attach is now a boolean and the cap is an integer, so
there is exactly one way to say each thing.
"""

from __future__ import annotations

from core.agent_harness.session.session_goal import session_goal_from_handoffs
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
    """Single-turn asks must not pick up an outer goal."""
    handoff = AssistantHandoff.from_tool_input({"content": "One answer.", "session_goal": False})

    assert handoff.session_goal is False
    assert session_goal_from_handoffs(handoff.to_handoff_contents(), condition="x") is None

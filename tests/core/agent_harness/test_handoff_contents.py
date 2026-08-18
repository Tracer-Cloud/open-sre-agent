"""Expand assistant_handoff tool inputs via the typed AssistantHandoff model."""

from __future__ import annotations

from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.handoff_contents import handoff_contents_from_tool_input


def test_content_only_handoff_keeps_content_string() -> None:
    assert handoff_contents_from_tool_input({"content": "docs:help"}) == ("docs:help",)


def test_structured_evidence_kind_emits_clean_tag() -> None:
    """``metric_read`` emits a clean kind tag and implies SessionGoal attach.

    Incomplete metric answers need the host continuation loop; omitting
    ``session_goal`` would exit after one gather/answer turn.
    """
    tags = handoff_contents_from_tool_input(
        {
            "content": "Count Windows users last 7 days.",
            "evidence_kind": "metric_read",
        }
    )
    assert tags == (
        "Count Windows users last 7 days.",
        "evidence_kind:metric_read",
        "session_goal:continue",
    )
    model = AssistantHandoff.from_tool_input(
        {
            "content": "Count Windows users last 7 days.",
            "evidence_kind": "metric_read",
        }
    )
    assert model.evidence_kind is not None
    assert model.evidence_kind.value == "metric_read"
    assert model.session_goal is True


def test_session_goal_fields_emit_attach_and_checklist_tags() -> None:
    tags = handoff_contents_from_tool_input(
        {
            "content": "Run the five-step checklist.",
            "session_goal": True,
            "session_goal_max_turns": 5,
            "session_goal_items": ["gather", "analyze", "report"],
        }
    )
    assert tags == (
        "Run the five-step checklist.",
        "session_goal:continue",
        "session_goal_max_turns:5",
        "session_goal_item:gather",
        "session_goal_item:analyze",
        "session_goal_item:report",
    )


def test_unknown_evidence_kind_is_ignored() -> None:
    tags = handoff_contents_from_tool_input(
        {"content": "hello", "evidence_kind": "not_a_real_kind"}
    )
    assert tags == ("hello",)

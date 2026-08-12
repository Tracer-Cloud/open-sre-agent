"""Typed AssistantHandoff: schema fields first, content-tag fallback."""

from __future__ import annotations

from core.agent_harness.session_goal.goal import session_goal_from_assistant_handoffs
from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.evidence_need import EvidenceKind, classify_evidence_need


def test_schema_field_wins_over_content_tag() -> None:
    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "evidence_kind=incident please",
            "evidence_kind": "metric_read",
        }
    )
    assert handoff.evidence_kind is EvidenceKind.METRIC_READ


def test_content_equals_tag_fills_missing_schema_field() -> None:
    handoff = AssistantHandoff.from_tool_input(
        {"content": "Count Windows users. evidence_kind=metric_read"}
    )
    assert handoff.evidence_kind is EvidenceKind.METRIC_READ
    assert "evidence_kind:metric_read" in handoff.to_handoff_contents()
    # Content-only metric_read must still derive session_goal attach — otherwise
    # run_until exits after one incomplete answer (live Windows-users miss).
    assert handoff.session_goal is True
    assert "session_goal:continue" in handoff.to_handoff_contents()


def test_metric_read_schema_field_implies_session_goal_attach() -> None:
    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "what windows users number last 7 days?",
            "evidence_kind": "metric_read",
        }
    )
    assert handoff.session_goal is True
    goal = session_goal_from_assistant_handoffs(
        (handoff,), condition="what windows users number last 7 days?"
    )
    assert goal is not None


def test_metric_read_explicit_session_goal_false_opts_out() -> None:
    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "one-shot count",
            "evidence_kind": "metric_read",
            "session_goal": False,
        }
    )
    assert handoff.session_goal is False
    assert session_goal_from_assistant_handoffs((handoff,)) is None


def test_session_goal_fields_decode_to_model() -> None:
    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "Run the checklist.",
            "session_goal": True,
            "session_goal_max_turns": 5,
            "session_goal_items": ["gather", "analyze"],
        }
    )
    assert handoff.session_goal is True
    assert handoff.session_goal_max_turns == 5
    assert handoff.session_goal_items == ("gather", "analyze")
    goal = session_goal_from_assistant_handoffs((handoff,))
    assert goal is not None
    assert goal.max_outer_turns == 5
    assert goal.checklist == ("gather", "analyze")


def test_classify_evidence_need_prefers_typed_handoffs() -> None:
    handoff = AssistantHandoff(
        content="Look up users.",
        evidence_kind=EvidenceKind.METRIC_READ,
    )
    need = classify_evidence_need(
        handoffs=(handoff,),
        # Deliberately wrong/missing tags — model fields must win.
        handoff_contents=("Look up users.",),
        preferred_sources_for=lambda _k: ("posthog",),
        resolved_integrations={},
    )
    assert need.kind is EvidenceKind.METRIC_READ
    assert need.tier.value == "L0_degraded"


def test_requires_gather_defaults_true() -> None:
    assert AssistantHandoff.from_tool_input({"content": "hi"}).requires_gather is True
    assert (
        AssistantHandoff.from_tool_input(
            {"content": "hi", "requires_gather": False}
        ).requires_gather
        is False
    )


def test_session_goal_content_tag_attaches_when_schema_omitted() -> None:
    handoff = AssistantHandoff.from_tool_input({"content": "session_goal:five_step_checklist"})
    assert handoff.session_goal is True
    assert "session_goal:continue" in handoff.to_handoff_contents()


def test_session_goal_achieved_content_tag_is_not_attach() -> None:
    handoff = AssistantHandoff.from_tool_input({"content": "Done.\nsession_goal:achieved"})
    assert handoff.session_goal is not True


def test_database_query_handoff_ignores_session_goal_attach() -> None:
    """Oracle 332: DB query/connect guidance is one-shot — no host goal loop."""
    handoff = AssistantHandoff.from_tool_input(
        {
            "content": "database_query:mysql_active_connections",
            "session_goal": True,
        }
    )
    assert handoff.session_goal is True  # decode keeps the flag
    assert (
        session_goal_from_assistant_handoffs(
            (handoff,),
            condition="Use the MySQL tool to query active connections.",
        )
        is None
    )

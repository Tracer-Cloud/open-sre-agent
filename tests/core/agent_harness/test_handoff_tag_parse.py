"""Plain-string handoff tag parsing (: / =, no regex)."""

from __future__ import annotations

from core.agent_harness.session_goal.goal import session_goal_from_handoffs
from core.agent_harness.turns.evidence_need import evidence_kind_from_handoffs
from core.agent_harness.turns.handoff_tag_parse import (
    find_tag_suffix,
    first_tag_token,
    handoff_has_tag,
)


def test_first_tag_token_accepts_colon_and_equals() -> None:
    assert first_tag_token("evidence_kind:metric_read", "evidence_kind") == "metric_read"
    assert first_tag_token("evidence_kind=metric_read", "evidence_kind") == "metric_read"


def test_first_tag_token_accepts_tag_then_prose() -> None:
    assert (
        first_tag_token(
            "evidence_kind:metric_read - PostHog query: Windows users.",
            "evidence_kind",
        )
        == "metric_read"
    )


def test_first_tag_token_finds_mid_content_boundary() -> None:
    assert (
        first_tag_token(
            "Look up Windows users. evidence_kind:metric_read",
            "evidence_kind",
        )
        == "metric_read"
    )
    assert (
        first_tag_token(
            "Look up Windows users. evidence_kind=metric_read",
            "evidence_kind",
        )
        == "metric_read"
    )


def test_first_tag_token_strips_backticks_and_punctuation() -> None:
    assert first_tag_token("`evidence_kind:metric_read`", "evidence_kind") == "metric_read"
    assert first_tag_token("evidence_kind:metric_read.", "evidence_kind") == "metric_read"


def test_first_tag_token_rejects_embedded_key_without_boundary() -> None:
    assert first_tag_token("myevidence_kind:metric_read", "evidence_kind") is None


def test_find_tag_suffix_keeps_session_goal_body_with_embedded_equals() -> None:
    assert (
        find_tag_suffix("session_goal=max_turns=5;steps=5", "session_goal") == "max_turns=5;steps=5"
    )
    assert (
        find_tag_suffix("session_goal:max_turns=5;steps=5", "session_goal") == "max_turns=5;steps=5"
    )


def test_session_goal_item_key_does_not_match_session_goal() -> None:
    assert find_tag_suffix("session_goal_item:gather metrics", "session_goal") is None
    assert find_tag_suffix("session_goal_item:gather metrics", "session_goal_item") == (
        "gather metrics"
    )


def test_evidence_kind_from_handoffs_equals_and_mid_prose() -> None:
    assert evidence_kind_from_handoffs(("evidence_kind=metric_read",)) == "metric_read"
    assert (
        evidence_kind_from_handoffs(("Please count users. evidence_kind=metric_read",))
        == "metric_read"
    )


def test_session_goal_from_handoffs_equals_form() -> None:
    goal = session_goal_from_handoffs(("session_goal=continue",))
    assert goal is not None
    assert goal.condition == "continue"
    goal = session_goal_from_handoffs(("session_goal=continue", "session_goal_max_turns:3"))
    assert goal is not None
    assert goal.max_outer_turns == 3


def test_handoff_has_tag_for_database_query_equals() -> None:
    assert handoff_has_tag("database_query=mysql_active_connections", "database_query")
    assert not handoff_has_tag("chat:greeting", "database_query")

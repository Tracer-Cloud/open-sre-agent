"""Prior-investigation summarization shared by the gather and answer prompts.

Prompt-shape behaviour (which block appears on which turn) lives in
``test_gather_prompt``; this pins the summarizer itself, including the paths
that must degrade rather than raise into a turn.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from core.agent_harness.prompts.memory import prior_investigation as pi


def test_headline_returns_only_the_fields_present() -> None:
    # Arrange / Act
    both = pi.prior_investigation_headline({"alert_name": "disk", "root_cause": "full"})
    cause_only = pi.prior_investigation_headline({"root_cause": "full"})

    # Assert
    assert both == ["Alert: disk", "Root cause: full"]
    assert cause_only == ["Root cause: full"]
    assert pi.prior_investigation_headline({}) == []


def test_summarize_includes_report_and_problem_summary() -> None:
    # Arrange
    state = {
        "alert_name": "Checkout 500s",
        "root_cause": "pool exhausted",
        "problem_md": "Checkout returned 500s after deploy.",
        "slack_message": "Investigation complete.",
    }

    # Act
    summary = pi.summarize(state)

    # Assert
    assert "Alert: Checkout 500s" in summary
    assert "Root cause: pool exhausted" in summary
    assert "Problem summary:\nCheckout returned 500s after deploy." in summary
    assert "Report:\nInvestigation complete." in summary


def test_summarize_reports_absence_rather_than_returning_empty() -> None:
    """An empty summary would render a heading with nothing under it."""
    # Arrange / Act / Assert
    assert pi.summarize({}) == "(no prior investigation details available)"


def test_summarize_caps_long_free_text() -> None:
    # Arrange: both fields are capped at 2000 chars, with a tail that must be cut.
    state = {
        "problem_md": "P" * 2000 + "PROBLEM-TAIL-CUT",
        "slack_message": "R" * 2000 + "REPORT-TAIL-CUT",
    }

    # Act
    summary = pi.summarize(state)

    # Assert
    assert "PROBLEM-TAIL-CUT" not in summary
    assert "REPORT-TAIL-CUT" not in summary
    assert "P" * 2000 in summary
    assert "R" * 2000 in summary


@pytest.mark.parametrize(
    ("evidence", "expected_fragment"),
    [
        ({"e1": {"summary": "pool maxed"}, "e2": {}}, "Evidence items: 2"),
        ([{"summary": "pool maxed"}], "Evidence items: 1"),
        ("a bare string", "Evidence type: str"),
    ],
)
def test_summarize_handles_each_evidence_shape(evidence: Any, expected_fragment: str) -> None:
    # Arrange / Act
    summary = pi.summarize({"root_cause": "x", "evidence": evidence})

    # Assert
    assert expected_fragment in summary


def test_summarize_survives_unserializable_evidence() -> None:
    """Evidence that cannot be serialized must not take down the turn."""
    # Arrange: a self-referential payload — json.dumps raises ValueError on it.
    circular: dict[str, Any] = {"marker": "circular-evidence"}
    circular["self"] = circular

    # Act
    summary = pi.summarize({"root_cause": "still reported", "evidence": circular})

    # Assert: the root cause survives and the failure is reported, not raised.
    assert "Root cause: still reported" in summary
    assert "could not be serialized" in summary


def test_follow_up_tag_is_recognized_by_prefix_not_exact_value() -> None:
    # Arrange / Act / Assert: the model may emit a suffix after the prefix.
    assert pi.is_prior_investigation_follow_up(("follow_up:prior_investigation",)) is True
    assert pi.is_prior_investigation_follow_up(("follow_up:anything_else",)) is True
    assert pi.is_prior_investigation_follow_up(("provider:local_llama_connect",)) is False
    assert pi.is_prior_investigation_follow_up(()) is False


def test_recall_window_rejects_state_it_cannot_date() -> None:
    """Fail open: undatable state gathers fresh evidence rather than suppressing tools."""
    # Arrange / Act / Assert
    assert pi.is_within_recall_window(None) is False
    assert pi.is_within_recall_window({}) is False
    assert pi.is_within_recall_window({"investigation_started_at": "not-a-number"}) is False
    # bool is an int subclass — it must not pass as a timestamp.
    assert pi.is_within_recall_window({"investigation_started_at": True}) is False


def test_recall_window_rejects_a_future_timestamp() -> None:
    # Arrange: a clock reading from another process reads as negative age.
    future = {"investigation_started_at": time.monotonic() + 600}

    # Act / Assert
    assert pi.is_within_recall_window(future) is False


def test_recall_window_accepts_a_just_finished_investigation() -> None:
    # Arrange / Act / Assert
    assert pi.is_within_recall_window({"investigation_started_at": time.monotonic()}) is True


def test_block_labels_stale_state_but_keeps_the_findings() -> None:
    # Arrange
    old = {
        "root_cause": "aging-root-cause",
        "investigation_started_at": time.monotonic() - pi.PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
    }

    # Act
    block = pi.build_block(old)

    # Assert
    assert "aging-root-cause" in block
    assert block.startswith(pi.STALE_PRIOR_INVESTIGATION_NOTE)


def test_block_omits_the_stale_label_on_an_explicit_follow_up() -> None:
    """The planner's tag outranks the clock — the RCA is the answer, not background."""
    # Arrange
    old = {
        "root_cause": "the-incident-they-asked-about",
        "investigation_started_at": time.monotonic() - pi.PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
    }

    # Act
    block = pi.build_block(old, explicit_follow_up=True)

    # Assert
    assert "the-incident-they-asked-about" in block
    assert pi.STALE_PRIOR_INVESTIGATION_NOTE not in block


def test_block_is_empty_without_state() -> None:
    # Arrange / Act / Assert
    assert pi.build_block(None) == ""

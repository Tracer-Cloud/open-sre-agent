from __future__ import annotations

from app.cli.ui.renderer.formatting import (
    format_prior_tools_clause,
    investigation_llm_progress_hint,
)


def test_investigation_llm_progress_hint_first_lap() -> None:
    assert investigation_llm_progress_hint(0, max_loops=20) == "Planning investigation (lap 1/20)"


def test_investigation_llm_progress_hint_later_laps() -> None:
    assert investigation_llm_progress_hint(1, max_loops=20) == "Reviewing evidence (lap 2/20)"
    assert investigation_llm_progress_hint(4, max_loops=20) == "Reviewing evidence (lap 5/20)"


def test_investigation_llm_progress_hint_includes_prior_tools() -> None:
    hint = investigation_llm_progress_hint(
        2,
        max_loops=20,
        prior_tools=["Datadog Logs", "Grafana Metrics"],
    )
    assert hint == "Reviewing evidence (lap 3/20) after Datadog Logs, Grafana Metrics"


def test_format_prior_tools_clause_dedupes_and_counts() -> None:
    clause = format_prior_tools_clause(
        ["Datadog Logs", "Datadog Logs", "Grafana Metrics"],
    )
    assert clause == " after Datadog Logs x2, Grafana Metrics"


def test_format_prior_tools_clause_truncates_long_lists() -> None:
    clause = format_prior_tools_clause(
        ["Tool A", "Tool B", "Tool C", "Tool D"],
        max_tools=2,
    )
    assert clause == " after Tool A, Tool B, ..."

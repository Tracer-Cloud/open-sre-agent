"""Tests for KQL take/limit pipe-stage text helpers."""

from __future__ import annotations

from integrations.azure.tools.azure_monitor_logs_tool._kql import (
    find_take_or_limit_values,
    has_take_or_limit_clause,
)


def test_detects_real_take_and_limit_pipe_stages() -> None:
    assert has_take_or_limit_clause("AppTraces | take 5") is True
    assert has_take_or_limit_clause("AppTraces | limit 5") is True
    assert find_take_or_limit_values("AppTraces | take 5") == [5]


def test_no_clause_returns_false_and_empty() -> None:
    assert has_take_or_limit_clause("AppTraces | order by TimeGenerated desc") is False
    assert find_take_or_limit_values("AppTraces | order by TimeGenerated desc") == []


def test_ignores_take_text_inside_a_quoted_string() -> None:
    query = 'AppTraces | where Message contains "take 5 minutes"'
    assert has_take_or_limit_clause(query) is False
    assert find_take_or_limit_values(query) == []


def test_ignores_pipe_and_take_text_embedded_in_a_quoted_string() -> None:
    """A literal '|' immediately before 'take N' inside a string is not a
    real pipe-stage boundary -- a pipe-anchored regex alone still matches
    it, so the quoted content must be masked first."""
    query = 'AppTraces | where Message contains "| take 5 now"'
    assert has_take_or_limit_clause(query) is False
    assert find_take_or_limit_values(query) == []


def test_ignores_take_text_inside_a_line_comment() -> None:
    query = "AppTraces | take 10 // fallback: | take 5 if unbounded"
    assert find_take_or_limit_values(query) == [10]


def test_finds_multiple_real_clauses() -> None:
    query = "AppTraces | where Level == 'Error' | take 5 | project Message"
    assert find_take_or_limit_values(query) == [5]


def test_single_quoted_string_is_also_masked() -> None:
    query = "AppTraces | where Level == '| take 5'"
    assert has_take_or_limit_clause(query) is False


def test_escaped_quote_does_not_end_the_string_early() -> None:
    """Regression: an escaped quote (``\\"``) inside a string must not be
    treated as the string's closing quote -- doing so exposes the rest of
    the literal (which may contain '| take N') as if it were real code."""
    query = 'AppTraces | where Message contains "escaped \\" quote | take 5 in it"'
    assert has_take_or_limit_clause(query) is False
    assert find_take_or_limit_values(query) == []


def test_escaped_backslash_before_closing_quote_still_closes_string() -> None:
    """A literal backslash (``\\\\``) escapes itself, not the following
    quote -- the string must still close normally afterward."""
    query = 'AppTraces | where Message contains "trailing backslash \\\\" | take 5'
    assert find_take_or_limit_values(query) == [5]

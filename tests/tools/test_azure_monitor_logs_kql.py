"""Tests for KQL row-capping pipe-stage text helpers."""

from __future__ import annotations

from integrations.azure.tools.azure_monitor_logs_tool._kql import (
    find_row_cap_values,
    has_row_cap_clause,
)


def test_detects_real_take_and_limit_pipe_stages() -> None:
    assert has_row_cap_clause("AppTraces | take 5") is True
    assert has_row_cap_clause("AppTraces | limit 5") is True
    assert find_row_cap_values("AppTraces | take 5") == [5]


def test_no_clause_returns_false_and_empty() -> None:
    assert has_row_cap_clause("AppTraces | order by TimeGenerated desc") is False
    assert find_row_cap_values("AppTraces | order by TimeGenerated desc") == []


def test_ignores_take_text_inside_a_quoted_string() -> None:
    query = 'AppTraces | where Message contains "take 5 minutes"'
    assert has_row_cap_clause(query) is False
    assert find_row_cap_values(query) == []


def test_ignores_pipe_and_take_text_embedded_in_a_quoted_string() -> None:
    """A literal '|' immediately before 'take N' inside a string is not a
    real pipe-stage boundary -- a pipe-anchored regex alone still matches
    it, so the quoted content must be masked first."""
    query = 'AppTraces | where Message contains "| take 5 now"'
    assert has_row_cap_clause(query) is False
    assert find_row_cap_values(query) == []


def test_ignores_take_text_inside_a_line_comment() -> None:
    query = "AppTraces | take 10 // fallback: | take 5 if unbounded"
    assert find_row_cap_values(query) == [10]


def test_finds_multiple_real_clauses() -> None:
    query = "AppTraces | where Level == 'Error' | take 5 | project Message"
    assert find_row_cap_values(query) == [5]


def test_single_quoted_string_is_also_masked() -> None:
    query = "AppTraces | where Level == '| take 5'"
    assert has_row_cap_clause(query) is False


def test_escaped_quote_does_not_end_the_string_early() -> None:
    """Regression: an escaped quote (``\\"``) inside a string must not be
    treated as the string's closing quote -- doing so exposes the rest of
    the literal (which may contain '| take N') as if it were real code."""
    query = 'AppTraces | where Message contains "escaped \\" quote | take 5 in it"'
    assert has_row_cap_clause(query) is False
    assert find_row_cap_values(query) == []


def test_escaped_backslash_before_closing_quote_still_closes_string() -> None:
    """A literal backslash (``\\\\``) escapes itself, not the following
    quote -- the string must still close normally afterward."""
    query = 'AppTraces | where Message contains "trailing backslash \\\\" | take 5'
    assert find_row_cap_values(query) == [5]


def test_verbatim_string_does_not_treat_backslash_as_an_escape() -> None:
    """Regression: KQL verbatim strings (``@"..."``) don't use backslash
    escaping -- a backslash there is a literal character, so the very next
    quote really does close the string. Treating it like a regular string's
    escape would extend the mask past the string's true end and hide a
    real take/limit stage that follows it, understating saturation."""
    query = 'AppTraces | where Message == @"literal \\" | take 5'
    assert find_row_cap_values(query) == [5]


def test_verbatim_string_uses_doubled_quote_to_escape() -> None:
    """A doubled quote (``""``) inside a verbatim string is its escape for
    a literal quote -- it must not end the string early, so fake take/limit
    text between the doubled quotes stays masked while a real clause after
    the string's true end is still detected."""
    query = 'AppTraces | where Message == @"a "" take 5 "" b" | take 3'
    assert find_row_cap_values(query) == [3]


def test_detects_top_by_clause() -> None:
    """Regression (review finding): `top N by ...` caps rows the same way
    `take` does. A query with only a trailing `take 50` after a `top 5 by
    ...` stage still returns at most 5 rows -- missing this operator would
    let a saturated result get cited as an exact count."""
    query = "AppTraces | top 5 by TimeGenerated desc | take 50"
    assert has_row_cap_clause(query) is True
    assert find_row_cap_values(query) == [5, 50]


def test_detects_sample_clause() -> None:
    query = "AppTraces | sample 20"
    assert has_row_cap_clause(query) is True
    assert find_row_cap_values(query) == [20]


def test_top_without_by_is_not_matched() -> None:
    """`top N` alone isn't valid KQL (it requires `by <expr>`) -- a bare
    `top N` shouldn't be mistaken for a real row-cap stage."""
    query = "AppTraces | top 5"
    assert has_row_cap_clause(query) is False


def test_ignores_top_by_text_inside_a_quoted_string() -> None:
    query = 'AppTraces | where Message contains "top 5 by errors"'
    assert has_row_cap_clause(query) is False

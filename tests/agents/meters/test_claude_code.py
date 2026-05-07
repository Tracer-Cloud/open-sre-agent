"""Tests for the Claude Code token meter (issue #1495)."""

from __future__ import annotations

import pathlib

import pytest

from app.agents.meters.claude_code import ClaudeCodeMeter

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "claude_code_stream.ndjson"


@pytest.fixture
def meter() -> ClaudeCodeMeter:
    return ClaudeCodeMeter()


def test_parses_full_fixture_stream(meter: ClaudeCodeMeter) -> None:
    """Sum input + output tokens across every ``assistant`` event in a
    real stream.

    Hand-counted from ``fixtures/claude_code_stream.ndjson``:

    - ``system.init`` → no usage block, contributes 0.
    - ``assistant`` msg_01: 120 in + 18 out = 138.
    - ``assistant`` msg_02: 250 in + 42 out = 292.
    - ``user`` (tool_result) → no usage block, contributes 0.
    - ``assistant`` msg_03: 315 in + 11 out = 326.
    - ``result`` → cumulative session totals (315 in + 71 out); the
      meter ignores ``result`` events because counting them would
      double-count the final turn's input and the entire session's
      output (~50% inflation in any multi-turn session).

    Total: 138 + 292 + 326 = **756**.
    """
    chunk = _FIXTURE.read_text(encoding="utf-8")
    assert meter.parse_chunk(chunk) == 756


def test_result_event_is_ignored(meter: ClaudeCodeMeter) -> None:
    """The ``result`` event carries cumulative session totals, not
    per-turn deltas — counting it would overcount. Locking the
    behavior in so a future "simplification" doesn't silently
    re-introduce a ~50% inflation in every multi-turn session.
    """
    result_event = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"duration_ms":3420,"usage":{"input_tokens":315,"output_tokens":71},'
        '"total_cost_usd":0.012}'
    )
    assert meter.parse_chunk(result_event) == 0


def test_returns_zero_for_irrelevant_chunk(meter: ClaudeCodeMeter) -> None:
    """Acceptance: irrelevant chunks return 0, not -1, not None, not a raise."""
    assert meter.parse_chunk("hello world\n") == 0
    assert meter.parse_chunk("") == 0
    assert meter.parse_chunk('{"type":"system","subtype":"init"}') == 0


def test_returns_zero_for_assistant_text_containing_token_keys(meter: ClaudeCodeMeter) -> None:
    """An assistant response whose ``text`` content happens to embed
    the literal JSON-key form (e.g. Claude generating documentation
    about the Anthropic API) must not contribute. Structural
    discrimination via ``message.usage`` rules out free-form text
    matches that a flat regex would have captured.
    """
    embedded_key = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"Anthropic responses look like '
        r'\"input_tokens\": 5000."}]}}'
    )
    assert meter.parse_chunk(embedded_key) == 0


def test_returns_zero_for_token_word_outside_json_key_form(meter: ClaudeCodeMeter) -> None:
    """Free-form 'tokens' mentions in assistant content must not be counted."""
    free_form = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"This used 50 tokens, roughly."}]}}'
    )
    assert meter.parse_chunk(free_form) == 0


def test_sums_correctly_across_split_chunks(meter: ClaudeCodeMeter) -> None:
    """A stream split into line-aligned chunks must total to the same
    as the full stream. The wiring layer reads ``stdout`` line-by-line,
    so this is the realistic splitting case.
    """
    full = _FIXTURE.read_text(encoding="utf-8")
    lines = full.splitlines(keepends=True)
    midpoint = len(lines) // 2
    chunk_a = "".join(lines[:midpoint])
    chunk_b = "".join(lines[midpoint:])
    assert meter.parse_chunk(chunk_a) + meter.parse_chunk(chunk_b) == 756


def test_handles_each_event_type_in_isolation(meter: ClaudeCodeMeter) -> None:
    """Each NDJSON event is independently parseable — useful for the
    line-by-line streaming the dashboard wiring will do.

    Per-line breakdown of the fixture (system, three assistant turns,
    a tool_result user event, and a final result event):
    """
    lines = _FIXTURE.read_text(encoding="utf-8").splitlines()
    counts = [meter.parse_chunk(line) for line in lines]
    # system, msg_01, msg_02, tool_result, msg_03, result
    assert counts == [0, 138, 292, 0, 326, 0]


def test_cache_token_counters_are_not_summed(meter: ClaudeCodeMeter) -> None:
    """``cache_creation_input_tokens`` and ``cache_read_input_tokens``
    are deliberately ignored — they're billed at different rates and
    the dashboard's ``$/hr`` column will need them broken out
    separately when cache-cost tracking ships in a follow-up.
    """
    chunk_with_cache = (
        '{"type":"assistant","message":{"usage":{"input_tokens":100,'
        '"cache_creation_input_tokens":500,"cache_read_input_tokens":2000,'
        '"output_tokens":50}}}'
    )
    # 100 + 50 = 150, NOT 100 + 500 + 2000 + 50 = 2650
    assert meter.parse_chunk(chunk_with_cache) == 150


def test_malformed_json_lines_are_skipped(meter: ClaudeCodeMeter) -> None:
    """Truncated or otherwise unparseable JSON lines must not raise —
    the wiring layer can deliver partial lines on subprocess
    teardown, and a noisy session should not crash the dashboard.
    """
    chunk = (
        "not json at all\n"
        '{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":5}}}\n'
        '{"type":"assistant","message":{"usage":'  # truncated
    )
    assert meter.parse_chunk(chunk) == 15

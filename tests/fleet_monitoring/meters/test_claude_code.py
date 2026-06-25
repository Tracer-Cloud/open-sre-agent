"""Tests for the Claude Code token meter (issue #1495)."""

from __future__ import annotations

import pathlib

import pytest

from tools.fleet_monitoring.meters.claude_code import ClaudeCodeMeter

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
    are included in visible activity and broken out for exact pricing.
    """
    chunk_with_cache = (
        '{"type":"assistant","message":{"usage":{"input_tokens":100,'
        '"cache_creation_input_tokens":500,"cache_read_input_tokens":2000,'
        '"output_tokens":50}}}'
    )
    sample = meter.sample_chunk(chunk_with_cache)
    assert sample.tokens == 2650
    assert sample.usage.input_tokens == 100
    assert sample.usage.cache_creation_input_tokens == 500
    assert sample.usage.cache_read_input_tokens == 2000
    assert sample.usage.output_tokens == 50


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


def test_sample_chunk_surfaces_latest_assistant_model(meter: ClaudeCodeMeter) -> None:
    """``sample_chunk`` (#2023) returns the model from the *latest*
    assistant event so pricing follows the active model. A mid-stream
    ``/model`` switch should be reflected immediately rather than
    sticking on the first model seen.
    """
    chunk = "\n".join(
        [
            (
                '{"type":"assistant","message":{"model":"claude-sonnet-4-5",'
                '"usage":{"input_tokens":10,"output_tokens":5}}}'
            ),
            (
                '{"type":"assistant","message":{"model":"claude-opus-4-1",'
                '"usage":{"input_tokens":20,"output_tokens":8}}}'
            ),
        ]
    )
    sample = meter.sample_chunk(chunk)
    assert sample.tokens == 43
    assert sample.model == "claude-opus-4-1"


def test_sample_chunk_returns_none_model_when_assistant_lacks_model_field(
    meter: ClaudeCodeMeter,
) -> None:
    """Older fixtures and some Claude Code releases omit
    ``message.model`` on assistant events. ``sample_chunk`` must
    accept that without raising and surface ``model=None`` — the
    wiring layer then falls back to yaml override or env var.
    """
    chunk = '{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":5}}}'
    sample = meter.sample_chunk(chunk)
    assert sample.tokens == 15
    assert sample.model is None


def test_sample_chunk_ignores_model_on_non_assistant_events(meter: ClaudeCodeMeter) -> None:
    """The ``system.init`` event carries a top-level ``model`` field
    but is not an assistant event. The meter must not surface it from
    a chunk with no assistant turns, or the dashboard would price an
    empty window against a model the user has not yet used.
    """
    chunk = '{"type":"system","subtype":"init","session_id":"abc","model":"claude-opus-4-7"}'
    sample = meter.sample_chunk(chunk)
    assert sample.tokens == 0
    assert sample.model is None


def test_sample_chunk_tokens_match_parse_chunk_for_back_compat(meter: ClaudeCodeMeter) -> None:
    """Locking the invariant that the integer-only API and the
    structured API agree on the token count — any callers still using
    ``parse_chunk`` must continue to see the same numbers.
    """
    chunk = _FIXTURE.read_text(encoding="utf-8")
    assert meter.parse_chunk(chunk) == meter.sample_chunk(chunk).tokens

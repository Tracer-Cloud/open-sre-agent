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
    """Sum input + output tokens across every message in a real stream.

    Hand-counted from ``fixtures/claude_code_stream.ndjson``:
    - msg_01: 120 in + 18 out = 138
    - msg_02: 250 in + 42 out = 292
    - msg_03: 315 in + 11 out = 326
    - result: 315 in + 71 out = 386 (the ``result`` event repeats the
      final-turn totals, which the meter correctly counts again — the
      dashboard wiring is responsible for de-duplicating, not the
      parser)

    Total: 138 + 292 + 326 + 386 = 1142.
    """
    chunk = _FIXTURE.read_text(encoding="utf-8")
    assert meter.parse_chunk(chunk) == 1142


def test_returns_zero_for_irrelevant_chunk(meter: ClaudeCodeMeter) -> None:
    """Acceptance: irrelevant chunks return 0, not -1, not None, not a raise."""
    assert meter.parse_chunk("hello world\n") == 0
    assert meter.parse_chunk("") == 0
    assert meter.parse_chunk('{"type":"system","subtype":"init"}') == 0


def test_returns_zero_for_token_word_outside_json_key_form(meter: ClaudeCodeMeter) -> None:
    """Free-form 'tokens' mentions in assistant content must not be counted.

    Previously a regex that matched any ``tokens`` substring would
    falsely score the assistant's own prose. The quoted-key form is
    the contract.
    """
    free_form = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"This used 50 tokens, roughly."}]}}'
    )
    assert meter.parse_chunk(free_form) == 0


def test_sums_correctly_across_split_chunks(meter: ClaudeCodeMeter) -> None:
    """Acceptance: a stream split into multiple chunks must total to the
    same as the full stream when partial chunks don't bisect a
    ``"input_tokens": <n>`` match.

    The dashboard wiring delivers chunks aligned on newlines (it reads
    ``stdout`` line-by-line under the hood), so this is the realistic
    splitting case.
    """
    full = _FIXTURE.read_text(encoding="utf-8")
    lines = full.splitlines(keepends=True)
    # Split mid-stream: first half + second half, line-aligned.
    midpoint = len(lines) // 2
    chunk_a = "".join(lines[:midpoint])
    chunk_b = "".join(lines[midpoint:])
    assert meter.parse_chunk(chunk_a) + meter.parse_chunk(chunk_b) == 1142


def test_handles_each_event_type_in_isolation(meter: ClaudeCodeMeter) -> None:
    """Each NDJSON event is independently parseable — useful for the
    line-by-line streaming the dashboard wiring will do."""
    lines = _FIXTURE.read_text(encoding="utf-8").splitlines()
    counts = [meter.parse_chunk(line) for line in lines]
    # init has no usage → 0; first assistant has 120+18=138; etc.
    assert counts == [0, 138, 292, 0, 326, 386]


def test_cache_token_counters_are_not_summed(meter: ClaudeCodeMeter) -> None:
    """``cache_creation_input_tokens`` and ``cache_read_input_tokens``
    are deliberately ignored — they're billed at different rates and
    the dashboard's ``$/hr`` column will need them broken out
    separately when cache-cost tracking ships in a follow-up.
    """
    chunk_with_cache = (
        '{"usage":{"input_tokens":100,"cache_creation_input_tokens":500,'
        '"cache_read_input_tokens":2000,"output_tokens":50}}'
    )
    # 100 + 50 = 150, NOT 100 + 500 + 2000 + 50 = 2650
    assert meter.parse_chunk(chunk_with_cache) == 150

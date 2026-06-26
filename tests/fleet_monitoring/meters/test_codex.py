"""Tests for the Codex token meter (#2023).

Fixtures and assertions are derived from a real codex-cli 0.130.0
rollout captured during the issue-#2023 demo. The on-disk format
uses ``event_msg`` with ``payload.type == "token_count"`` and a
nested ``payload.info.last_token_usage`` block — distinct from the
``codex exec --json`` stdout format which uses ``turn.completed``.
"""

from __future__ import annotations

import pathlib

import pytest

from tools.fleet_monitoring.meters.codex import CodexMeter

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "codex_rollout.ndjson"


@pytest.fixture
def meter() -> CodexMeter:
    return CodexMeter()


def test_parses_full_fixture_rollout(meter: CodexMeter) -> None:
    """Sum ``input_tokens + output_tokens`` across every per-turn
    ``token_count`` event in a realistic Codex rollout fixture.

    Hand-counted from ``fixtures/codex_rollout.ndjson``:

    - ``session_meta`` → no usage, contributes 0.
    - ``turn_context`` (t_001) → no usage.
    - ``event_msg`` token_count #1 → ``info: null`` (session-start
      handshake), contributes 0.
    - ``response_item`` → no usage.
    - ``event_msg`` token_count for t_001: 120 in + 18 out = 138.
    - ``turn_context`` (t_002) → no usage.
    - ``event_msg`` token_count for t_002: 250 in + 42 out = 292.
      ``cached_input_tokens: 100`` is NOT summed.
    - ``turn_context`` (t_003) → no usage.
    - ``event_msg`` token_count for t_003: 315 in + 11 out = 326.
      ``reasoning_output_tokens: 50`` is NOT summed.

    Total: 138 + 292 + 326 = **756**. Identical to the Claude Code
    fixture total — intentional, makes cross-meter comparison easy.
    """
    chunk = _FIXTURE.read_text(encoding="utf-8")
    assert meter.parse_chunk(chunk) == 756


def test_info_null_token_count_event_returns_zero(meter: CodexMeter) -> None:
    """The session-start ``token_count`` event carries ``info: null``
    while the rate-limit handshake completes. Counting it would
    raise ``AttributeError`` on the ``.get`` call; treating it as 0
    is both correct and crash-safe.
    """
    chunk = (
        '{"type":"event_msg","payload":'
        '{"type":"token_count","info":null,"rate_limits":{"plan_type":"plus"}}}'
    )
    assert meter.parse_chunk(chunk) == 0


def test_cached_input_tokens_are_not_summed(meter: CodexMeter) -> None:
    """``cached_input_tokens`` is a discounted subset of input,
    so it is exposed for pricing but not added to visible tokens.
    """
    chunk = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"last_token_usage":'
        '{"input_tokens":100,"cached_input_tokens":500,'
        '"output_tokens":50,"reasoning_output_tokens":0,"total_tokens":150}}}}'
    )
    # 100 + 50 = 150, NOT 100 + 500 + 50 = 650.
    sample = meter.sample_chunk(chunk)
    assert sample.tokens == 150
    assert sample.usage.input_tokens == 100
    assert sample.usage.cached_input_tokens == 500
    assert sample.usage.output_tokens == 50


def test_reasoning_output_tokens_are_not_summed(meter: CodexMeter) -> None:
    """``reasoning_output_tokens`` (o-series internal CoT) bill at
    the output rate but only some models emit them. Excluding them
    keeps non-reasoning models from being penalized by a
    presence-vs-absence inconsistency.
    """
    chunk = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"last_token_usage":'
        '{"input_tokens":100,"cached_input_tokens":0,"output_tokens":50,'
        '"reasoning_output_tokens":300,"total_tokens":150}}}}'
    )
    # 100 + 50 = 150, NOT 100 + 50 + 300 = 450.
    assert meter.parse_chunk(chunk) == 150


def test_total_token_usage_is_not_summed(meter: CodexMeter) -> None:
    """``total_token_usage`` is cumulative across the session.
    Summing it would double-count every turn from the second tick on.
    Lock the meter's choice of ``last_token_usage`` in.
    """
    chunk = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"last_token_usage":'
        '{"input_tokens":10,"cached_input_tokens":0,"output_tokens":5,'
        '"reasoning_output_tokens":0,"total_tokens":15},'
        '"total_token_usage":'
        '{"input_tokens":9999,"output_tokens":9999,"total_tokens":19998}}}}'
    )
    # Only the per-turn delta contributes.
    assert meter.parse_chunk(chunk) == 15


def test_total_token_usage_fallback_initializes_baseline(meter: CodexMeter) -> None:
    """When Codex omits ``last_token_usage``, the first cumulative
    total for a PID becomes the baseline and must not retro-price the
    session history.
    """
    chunk = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"total_token_usage":'
        '{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":200}}}}'
    )
    sample = meter.sample_chunk(chunk, pid=4242)
    assert sample.tokens == 0
    assert sample.usage.input_tokens == 0


def test_total_token_usage_fallback_counts_delta(meter: CodexMeter) -> None:
    first = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"total_token_usage":'
        '{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":200}}}}'
    )
    second = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"total_token_usage":'
        '{"input_tokens":1125,"cached_input_tokens":450,"output_tokens":240}}}}'
    )
    assert meter.sample_chunk(first, pid=4242).tokens == 0
    sample = meter.sample_chunk(second, pid=4242)
    assert sample.tokens == 165
    assert sample.usage.input_tokens == 125
    assert sample.usage.cached_input_tokens == 50
    assert sample.usage.output_tokens == 40


def test_total_token_usage_reset_rebaselines_without_delta(meter: CodexMeter) -> None:
    first = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"total_token_usage":'
        '{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":200}}}}'
    )
    reset = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"total_token_usage":'
        '{"input_tokens":25,"cached_input_tokens":10,"output_tokens":5}}}}'
    )
    assert meter.sample_chunk(first, pid=4242).tokens == 0
    assert meter.sample_chunk(reset, pid=4242).tokens == 0
    assert meter.known_pids() == [4242]
    meter.forget(4242)
    assert meter.known_pids() == []


def test_non_token_count_events_return_zero(meter: CodexMeter) -> None:
    """``session_meta``, ``turn_context``, and ``response_item``
    events do not carry per-turn usage. Skipping them is what keeps
    the meter monotonic.
    """
    irrelevant = "\n".join(
        [
            '{"type":"session_meta","payload":{"id":"th_1","model_provider":"openai"}}',
            '{"type":"turn_context","payload":{"turn_id":"t_1","model":"gpt-5"}}',
            '{"type":"response_item","payload":{"id":"i_1","type":"agent_message","text":"hi"}}',
        ]
    )
    assert meter.parse_chunk(irrelevant) == 0


def test_returns_zero_for_irrelevant_chunk(meter: CodexMeter) -> None:
    assert meter.parse_chunk("") == 0
    assert meter.parse_chunk("hello world\n") == 0
    assert meter.parse_chunk("not even close to json\n") == 0


def test_malformed_json_lines_are_skipped(meter: CodexMeter) -> None:
    """A noisy session must not crash — the wiring layer can deliver
    partial / truncated lines on subprocess teardown.
    """
    chunk = (
        "not json at all\n"
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"last_token_usage":{"input_tokens":10,"output_tokens":5}}}}\n'
        '{"type":"event_msg","payload":{"type":"token_count","info":'  # truncated mid-object
    )
    assert meter.parse_chunk(chunk) == 15


def test_sample_chunk_surfaces_latest_turn_context_model(meter: CodexMeter) -> None:
    """``sample_chunk`` returns ``model`` from the latest
    ``turn_context`` event so pricing follows the active model when
    a session migrates mid-stream (e.g. fallback from a paid model
    to a cheaper one).
    """
    chunk = "\n".join(
        [
            '{"type":"turn_context","payload":{"turn_id":"t_1","model":"gpt-5"}}',
            (
                '{"type":"event_msg","payload":{"type":"token_count","info":'
                '{"last_token_usage":{"input_tokens":10,"output_tokens":5}}}}'
            ),
            '{"type":"turn_context","payload":{"turn_id":"t_2","model":"gpt-5-codex"}}',
            (
                '{"type":"event_msg","payload":{"type":"token_count","info":'
                '{"last_token_usage":{"input_tokens":20,"output_tokens":10}}}}'
            ),
        ]
    )
    sample = meter.sample_chunk(chunk)
    assert sample.tokens == 45
    assert sample.model == "gpt-5-codex"


def test_sample_chunk_returns_none_model_when_no_turn_context(
    meter: CodexMeter,
) -> None:
    """``session_meta`` carries ``model_provider`` (e.g. ``"openai"``)
    but not the specific model id. The meter must not surface a
    placeholder; let pricing fall back to yaml / env var sources.
    """
    chunk = '{"type":"session_meta","payload":{"model_provider":"openai"}}'
    sample = meter.sample_chunk(chunk)
    assert sample.tokens == 0
    assert sample.model is None


def test_sample_chunk_tokens_match_parse_chunk_for_back_compat(meter: CodexMeter) -> None:
    """The ``int`` returned by ``parse_chunk`` must equal
    ``sample_chunk(...).tokens`` for every input.
    """
    chunk = _FIXTURE.read_text(encoding="utf-8")
    assert meter.parse_chunk(chunk) == meter.sample_chunk(chunk).tokens


def test_bool_input_tokens_does_not_add_one(meter: CodexMeter) -> None:
    """``bool`` is a subclass of ``int``; a stray ``true`` in
    malformed output must not silently add 1.
    """
    chunk = (
        '{"type":"event_msg","payload":{"type":"token_count","info":'
        '{"last_token_usage":{"input_tokens":true,"output_tokens":false}}}}'
    )
    assert meter.parse_chunk(chunk) == 0

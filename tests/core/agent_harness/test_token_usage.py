"""Unit tests for the session TokenUsage value object (extracted off Session)."""

from __future__ import annotations

from core.agent_harness.session.token_usage import TokenUsage


def test_record_accumulates_measured_totals_and_call_count() -> None:
    usage = TokenUsage()
    usage.record(input_tokens=100, output_tokens=40)
    usage.record(input_tokens=10, output_tokens=5)

    assert usage.totals["input"] == 110
    assert usage.totals["output"] == 45
    assert usage.totals["input_measured"] == 110
    assert usage.totals["output_measured"] == 45
    assert usage.call_count == 2
    assert usage.has_estimates is False


def test_record_estimated_sets_estimate_buckets_and_flag() -> None:
    usage = TokenUsage()
    usage.record(input_tokens=30, output_tokens=0, estimated=True)

    assert usage.totals["input"] == 30
    assert usage.totals["input_estimated"] == 30
    assert "output" not in usage.totals
    assert usage.has_estimates is True
    assert usage.call_count == 1


def test_record_noop_when_no_tokens() -> None:
    usage = TokenUsage()
    usage.record()
    assert usage.totals == {}
    assert usage.call_count == 0


def test_reset_clears_totals_and_count() -> None:
    usage = TokenUsage()
    usage.record(input_tokens=5, output_tokens=5)
    usage.reset()
    assert usage.totals == {}
    assert usage.call_count == 0

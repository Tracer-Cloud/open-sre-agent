"""Tests for ``scoring.calculate_total_latency`` — the MTTI source.

MTTI ("mean time to identify") was structurally 0 in every run: the scorer
summed per-step ``model_latency``/``tool_latency`` that the replay backend
never populates. The real wall-clock the runner measures
(``RunResult.latency_ms``) now flows into ``case_data["latency_ms"]`` and
becomes the preferred source. These tests pin that contract:

  - real measured wall-clock wins, converted ms -> seconds
  - per-step latencies are the fallback for future per-step instrumentation
  - neither present -> 0.0 (a missing measurement is visibly 0, never faked)
"""

from __future__ import annotations

from tests.benchmarks.cloudopsbench.scoring import calculate_total_latency


def test_prefers_measured_wall_clock_in_seconds() -> None:
    """latency_ms is the real monotonic measurement; report it in seconds."""
    assert calculate_total_latency({"latency_ms": 8200}) == 8.2


def test_wall_clock_wins_over_zero_step_latencies() -> None:
    """The replay backend writes tool_latency=0; the measured wall-clock must
    still surface rather than being drowned out by the zeros."""
    case_data = {
        "latency_ms": 5000,
        "steps": [{"tool_latency": 0.0}, {"tool_latency": 0.0}],
    }
    assert calculate_total_latency(case_data) == 5.0


def test_falls_back_to_step_latencies_when_no_wall_clock() -> None:
    """Without a measured wall-clock, sum any per-step instrumentation."""
    case_data = {
        "steps": [
            {"model_latency": 1.5, "tool_latency": 0.2},
            {"model_latency": 0.8},
        ]
    }
    assert calculate_total_latency(case_data) == 2.5


def test_zero_when_neither_source_present() -> None:
    """A hand-built case_data with no timing yields a visible 0, not a guess."""
    assert calculate_total_latency({"steps": []}) == 0.0
    assert calculate_total_latency({}) == 0.0


def test_nonpositive_wall_clock_falls_through_to_steps() -> None:
    """A 0/negative latency_ms is treated as 'not measured' so the step-sum
    fallback (or 0) applies instead of reporting a bogus 0-second diagnosis."""
    case_data = {"latency_ms": 0, "steps": [{"model_latency": 3.0}]}
    assert calculate_total_latency(case_data) == 3.0

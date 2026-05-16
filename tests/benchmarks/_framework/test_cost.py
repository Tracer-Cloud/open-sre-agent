from __future__ import annotations

import pytest

from tests.benchmarks._framework.cost import estimate_case_cost_usd


def test_estimate_case_cost_uses_tool_tier_discount() -> None:
    total, breakdown = estimate_case_cost_usd(
        {
            "claude-sonnet-4": {"input_tokens": 1_000_000, "output_tokens": 0},
            "claude-3-haiku": {"input_tokens": 1_000_000, "output_tokens": 0},
        },
        llm="claude-4-sonnet",
    )

    assert breakdown["claude-sonnet-4"] == 3.0
    assert breakdown["claude-3-haiku"] == pytest.approx(0.6)
    assert total == pytest.approx(3.6)

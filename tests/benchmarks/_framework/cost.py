from __future__ import annotations

from typing import Any

from tests.benchmarks.toolcall_model_benchmark.pricing import estimate_run_cost_usd

DEFAULT_MODEL_PRICES_USD_PER_MTOK: dict[str, float] = {
    "claude-4-sonnet": 3.0,
    "deepseek-v3.2": 1.0,
    "gpt-5": 3.0,
    "gpt-4o": 2.5,
}


def token_count(value: Any) -> int:
    total = getattr(value, "total", None)
    if callable(total):
        return int(total())
    if isinstance(total, int):
        return total
    if isinstance(value, dict):
        return int(value.get("input_tokens", 0) or 0) + int(value.get("output_tokens", 0) or 0)
    return int(getattr(value, "input_tokens", 0) or 0) + int(
        getattr(value, "output_tokens", 0) or 0
    )


def tokens_by_model_from_state(state: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        state.get("tokens_by_model"),
        state.get("token_usage_by_model"),
        state.get("usage_by_model"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def estimate_case_cost_usd(
    tokens_by_model: dict[str, Any],
    *,
    llm: str,
) -> tuple[float, dict[str, float]]:
    if not tokens_by_model:
        return 0.0, {}
    if all(isinstance(value, dict) for value in tokens_by_model.values()):
        price = DEFAULT_MODEL_PRICES_USD_PER_MTOK.get(llm.lower(), 3.0)
        breakdown = {
            model_id: (token_count(value) / 1_000_000.0) * price
            for model_id, value in tokens_by_model.items()
        }
        return sum(breakdown.values()), breakdown
    price = DEFAULT_MODEL_PRICES_USD_PER_MTOK.get(llm.lower(), 3.0)
    return estimate_run_cost_usd(
        tokens_by_model,
        reasoning_model=llm,
        tool_model=llm,
        reasoning_usd_per_mtok=price,
        tool_usd_per_mtok=price,
    )

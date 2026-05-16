from __future__ import annotations

from typing import Any

DEFAULT_MODEL_PRICES_USD_PER_MTOK: dict[str, float] = {
    "claude-4-sonnet": 3.0,
    "deepseek-v3.2": 1.0,
    "gpt-5": 3.0,
    "gpt-4o": 2.5,
}
TOOL_TIER_PRICE_MULTIPLIER = 0.2


def _classify_pricing_tier(model_id: str, reasoning_model: str, tool_model: str) -> str:
    normalized = model_id.lower()
    if model_id == reasoning_model or normalized == reasoning_model.lower():
        return "reasoning"
    if model_id == tool_model or normalized == tool_model.lower():
        return "tool"
    if "haiku" in normalized:
        return "tool"
    if "sonnet" in normalized or "opus" in normalized:
        return "reasoning"
    return "reasoning"


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
    price = DEFAULT_MODEL_PRICES_USD_PER_MTOK.get(llm.lower(), 3.0)
    if all(isinstance(value, dict) for value in tokens_by_model.values()):
        breakdown = {}
        for model_id, value in tokens_by_model.items():
            tier = _classify_pricing_tier(model_id, reasoning_model=llm, tool_model=llm)
            rate = price if tier == "reasoning" else price * TOOL_TIER_PRICE_MULTIPLIER
            breakdown[model_id] = (token_count(value) / 1_000_000.0) * rate
        return sum(breakdown.values()), breakdown
    breakdown: dict[str, float] = {}
    for model_id, token_bucket in tokens_by_model.items():
        tier = _classify_pricing_tier(model_id, reasoning_model=llm, tool_model=llm)
        rate = price if tier == "reasoning" else price * TOOL_TIER_PRICE_MULTIPLIER
        breakdown[model_id] = (token_count(token_bucket) / 1_000_000.0) * rate
    return sum(breakdown.values()), breakdown

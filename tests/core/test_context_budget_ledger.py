"""TDD: context-budget token ledger (R2).

Hot-path cost: each think used to re-``json.dumps`` every content block and,
while trimming, re-estimate the whole transcript after every eviction.
A per-message ledger should estimate once, then adjust only touched indices.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from core.context_budget import (
    enforce_context_budget,
    estimate_message_tokens,
)


def _assistant_tool_use(call_id: str, name: str = "noop") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}],
    }


def _tool_result(call_id: str, body: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": call_id, "content": body}],
    }


def _over_budget_transcript(*, exchanges: int = 6, payload: int = 4_000) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": "alert"}]
    for index in range(exchanges):
        call_id = f"t{index}"
        messages.append(_assistant_tool_use(call_id))
        messages.append(_tool_result(call_id, ("x" if index % 2 == 0 else "y") * payload))
    return messages


def test_plain_string_messages_never_call_json_dumps_under_ceiling() -> None:
    messages = [
        {"role": "user", "content": "short alert"},
        {"role": "assistant", "content": "short answer"},
    ]
    dumps_calls = 0
    original = json.dumps

    def counting_dumps(value: object, *args: object, **kwargs: object) -> str:
        nonlocal dumps_calls
        dumps_calls += 1
        return original(value, *args, **kwargs)

    with patch("core.context_budget.json.dumps", side_effect=counting_dumps):
        enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=100_000)

    assert dumps_calls == 0


def test_trim_loop_does_not_reestimate_every_message_each_eviction() -> None:
    """After the initial ledger build, trims must not re-walk the whole list."""
    messages = _over_budget_transcript(exchanges=8, payload=3_000)
    ceiling = 800
    estimate_calls = 0
    original = None

    import core.context_budget as budget

    original = budget._message_token_estimate

    def counting_estimate(message: dict[str, Any]) -> int:
        nonlocal estimate_calls
        estimate_calls += 1
        return original(message)

    with patch.object(budget, "_message_token_estimate", side_effect=counting_estimate):
        enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=ceiling)

    # Initial build ≈ len(messages_before). Each truncate may re-estimate 1 msg.
    # A full re-estimate after every trim would be many multiples of M.
    # Bound: initial pass + at most a few per truncate, far below M * trim_count.
    assert estimate_calls < 40, (
        f"_message_token_estimate called {estimate_calls} times — "
        "trim loop appears to re-estimate the whole transcript each eviction"
    )
    assert estimate_message_tokens(messages) <= ceiling


def test_trim_loop_still_fits_under_ceiling() -> None:
    messages = _over_budget_transcript(exchanges=5, payload=4_000)
    ceiling = 500
    enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=ceiling)
    assert estimate_message_tokens(messages) <= ceiling
    assert len(messages) < 11


def test_candidate_exchange_uses_ledger_tokens_not_fresh_dumps() -> None:
    """While selecting eviction candidates, do not re-serialize exchange bodies."""
    messages = _over_budget_transcript(exchanges=4, payload=2_000)
    ceiling = 600
    dumps_calls = 0
    original = json.dumps

    def counting_dumps(value: object, *args: object, **kwargs: object) -> str:
        nonlocal dumps_calls
        dumps_calls += 1
        return original(value, *args, **kwargs)

    with patch("core.context_budget.json.dumps", side_effect=counting_dumps):
        enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=ceiling)

    # Initial ledger build dumps each tool_result block once (~4 exchanges * 1).
    # Without a ledger, every candidate scan + every re-estimate multiplies this.
    assert dumps_calls < 30, (
        f"json.dumps called {dumps_calls} times during enforce — "
        "candidate selection or trim re-serialize looks quadratic"
    )

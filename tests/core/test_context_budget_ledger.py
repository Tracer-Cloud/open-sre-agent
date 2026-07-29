"""Context-budget token ledger.

Each think used to re-``json.dumps`` every content block and, while trimming,
re-estimate the whole transcript after every eviction. A per-message ledger
should estimate once, then adjust only touched indices.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import core.context_budget as budget


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
        budget.enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=100_000)

    assert dumps_calls == 0


def test_trim_loop_does_not_reestimate_every_message_each_eviction() -> None:
    """After the initial ledger build, trims must not re-walk the whole list."""
    messages = _over_budget_transcript(exchanges=8, payload=3_000)
    ceiling = 800
    estimate_calls = 0
    original = budget._message_token_estimate

    def counting_estimate(message: dict[str, Any]) -> int:
        nonlocal estimate_calls
        estimate_calls += 1
        return original(message)

    with patch.object(budget, "_message_token_estimate", side_effect=counting_estimate):
        budget.enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=ceiling)

    # Initial build ≈ len(messages_before). Each truncate may re-estimate 1 msg.
    # A full re-estimate after every trim would be many multiples of M.
    # Bound: initial pass + at most a few per truncate, far below M * trim_count.
    assert estimate_calls < 40, (
        f"_message_token_estimate called {estimate_calls} times — "
        "trim loop appears to re-estimate the whole transcript each eviction"
    )
    assert budget.estimate_message_tokens(messages) <= ceiling


def test_trim_loop_still_fits_under_ceiling() -> None:
    messages = _over_budget_transcript(exchanges=5, payload=4_000)
    ceiling = 500
    budget.enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=ceiling)
    assert budget.estimate_message_tokens(messages) <= ceiling
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
        budget.enforce_context_budget(messages, fixed_overhead_tokens=0, ceiling=ceiling)

    # Initial ledger build dumps each tool_result block once (~4 exchanges * 1).
    # Without a ledger, every candidate scan + every re-estimate multiplies this.
    assert dumps_calls < 30, (
        f"json.dumps called {dumps_calls} times during enforce — "
        "candidate selection or trim re-serialize looks quadratic"
    )


def test_ledger_slices_match_a_fresh_estimate_after_mixed_trim() -> None:
    """The incremental ledger must never drift from a from-scratch estimate."""
    # Arrange: a transcript with pinned, duplicate, and trimmable exchanges.
    messages = [
        {"role": "user", "content": "question " * 50},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "using a tool"},
                {"type": "tool_use", "id": "t1", "name": "grep", "input": {"q": "x"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "hit " * 200}],
        },
        {"role": "assistant", "content": "answer " * 30},
    ]
    per_message = [budget.estimate_message_tokens([m]) for m in messages]

    # Act
    with_ledger = budget._tool_exchange_candidates(messages, message_tokens=per_message)
    from_scratch = budget._tool_exchange_candidates(messages)

    # Assert: same candidates, same token estimates either way.
    assert [(c.start, c.end) for c in with_ledger] == [(c.start, c.end) for c in from_scratch]
    for ledger_candidate, fresh in zip(with_ledger, from_scratch, strict=True):
        assert ledger_candidate.token_estimate == fresh.token_estimate

"""Scratch probe of the duplicate-action guard hooks (not a kept test)."""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.action_driver import with_duplicate_action_call_guard
from core.execution import ToolExecutionResult
from core.llm.types import ToolCall


def _request(name: str, payload: dict[str, Any]) -> Any:
    call = ToolCall(id="x", name=name, input=payload)
    return type("R", (), {"tool_call": call, "arguments": payload})()


def _drive(batches: list[list[tuple[str, dict[str, Any], bool]]]) -> list[str]:
    """Run batches through the guard; each call carries whether it would succeed."""
    hooks = with_duplicate_action_call_guard()
    executed: list[str] = []
    for batch in batches:
        hooks.before_tool_batch([ToolCall(id="x", name=n, input=p) for n, p, _ in batch])
        for name, payload, ok in batch:
            request = _request(name, payload)
            decision = hooks.before_tool_call(request)
            label = str(payload.get("command", payload.get("payload")))
            if decision is not None and decision.blocked:
                print(f"   {label}: BLOCKED")
                hooks.after_tool_call(
                    request, ToolExecutionResult(content="blocked", is_error=True)
                )
                continue
            print(f"   {label}: ran ok={ok}")
            executed.append(label)
            hooks.after_tool_call(request, ToolExecutionResult(content="out", is_error=not ok))
    return executed


HEALTH = ("slash_invoke", {"command": "/health", "args": []}, True)
INTEG = ("slash_invoke", {"command": "/integrations", "args": ["list"]}, True)
REMOTE = ("slash_invoke", {"command": "/remote", "args": []}, True)
INTEG_FAIL = ("slash_invoke", {"command": "/integrations", "args": ["list"]}, False)


def test_probe_mixed_replay_then_reemit_new() -> None:
    print("\nmixed replay ->", _drive([[HEALTH, INTEG], [HEALTH, REMOTE], [REMOTE]]))


def test_probe_partial_failure_then_reemit_succeeded_one() -> None:
    print("\npartial failure ->", _drive([[HEALTH, INTEG_FAIL], [HEALTH]]))


def test_probe_unguarded_batch_between_repeats() -> None:
    print("\nunguarded between ->", _drive([[HEALTH], [("other_tool", {}, True)], [HEALTH]]))

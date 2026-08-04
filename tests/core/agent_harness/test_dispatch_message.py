"""Characterization tests for ``AgentHarness.dispatch_message``."""

from __future__ import annotations

from typing import Any

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.agent_harness.turns.headless_dispatch import (
    HeadlessAgent,
    NullToolProvider,
    StaticReasoningClientProvider,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


class _Echo:
    def invoke_stream(self, prompt: str) -> Any:
        yield f"echo:{prompt}"


def _empty_action() -> ToolCallingTurnResult:
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=False,
        handled=False,
    )


def test_dispatch_message_requires_attached_agent() -> None:
    harness = AgentHarness(HarnessConfig(load_env=False, open_storage=False))
    try:
        harness.dispatch_message("hi")
    except RuntimeError as exc:
        assert "attach_agent" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_dispatch_message_reuses_attached_agent(monkeypatch: Any) -> None:
    calls: list[str] = []

    class _Agent:
        def dispatch(self, message: str) -> TurnResult:
            calls.append(message)
            return TurnResult(
                final_intent="cli_agent_handled",
                action_result=_empty_action(),
                assistant_response_text=f"ok:{message}",
                llm_run=object(),
            )

    harness = AgentHarness(HarnessConfig(load_env=False))
    harness.attach_agent(_Agent())  # type: ignore[arg-type]
    first = harness.dispatch_message("one")
    second = harness.dispatch_message("two")

    assert calls == ["one", "two"]
    assert first.assistant_response_text == "ok:one"
    assert second.assistant_response_text == "ok:two"


def test_headless_bind_turn_swaps_output() -> None:
    from core.agent_harness.turns.headless_adapters import BufferOutputSink

    first = BufferOutputSink()
    second = BufferOutputSink()
    agent = HeadlessAgent(
        tools=NullToolProvider(),
        output=first,
        reasoning=StaticReasoningClientProvider(client=_Echo()),
    )
    before_runner = agent._action_runner  # noqa: SLF001
    agent.bind_turn(output=second)
    assert agent._output is second  # noqa: SLF001
    assert agent._action_runner is not before_runner  # noqa: SLF001
    assert agent._action_runner.output is second  # noqa: SLF001

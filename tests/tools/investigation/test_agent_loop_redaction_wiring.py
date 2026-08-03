"""The investigation loop itself must share one redaction per tool payload.

``_redacted_view`` reuses the redaction done when a tool call is announced.
Unit-testing that helper does not prove the loop calls it — the loop could go
back to ``redact_tool_view(tc.input, output)`` and every unit test would still
pass, because the *output* is identical either way and only the work doubles.

So this drives one real iteration of ``ConnectedInvestigationAgent.run`` with a
stubbed LLM and stubbed tools, and counts how many times the loop walks a tool
input. It is the only test here that fails if the wiring is undone.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.llm.types import AgentLLMResponse, ToolCall
from tools.investigation.stages.gather_evidence import agent as agent_module
from tools.investigation.stages.gather_evidence.agent import ConnectedInvestigationAgent
from tools.investigation.stages.gather_evidence.tools import build_connected_tool_context

TOOL_INPUT: dict[str, Any] = {"query": "status:error", "api_key": "sk-should-be-hidden"}


def _tool_call() -> ToolCall:
    return ToolCall(id="call-1", name="query_logs", input=TOOL_INPUT)


class _LLM:
    """Requests one tool call, then concludes."""

    model = "fake-model"

    def __init__(self) -> None:
        self._invocations = 0

    def tool_schemas(self, _tools: Any) -> list[Any]:
        return []

    def build_assistant_message(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"role": "assistant", "content": ""}

    def build_tool_result_message(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"role": "tool", "content": ""}

    def invoke(self, *_args: Any, **_kwargs: Any) -> AgentLLMResponse:
        self._invocations += 1
        calls = [_tool_call()] if self._invocations == 1 else []
        return AgentLLMResponse(content="", tool_calls=calls)


class _Tracker:
    def start(self, *_a: object, **_k: object) -> None:
        """Ignore progress."""

    def record_tool_start(self, *_a: object, **_k: object) -> None:
        """Ignore progress."""

    def record_tool_end(self, *_a: object, **_k: object) -> None:
        """Ignore progress."""

    def complete(self, *_a: object, **_k: object) -> None:
        """Ignore progress."""


@pytest.fixture
def loop_harness(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Stub everything around the loop and record each top-level redaction walk."""
    walked: list[Any] = []
    real_redact = agent_module.redact_sensitive

    def _counting(value: Any) -> Any:
        walked.append(value)
        return real_redact(value)

    def _counting_view(tool_input: Any, output: Any = None) -> Any:
        return agent_module.RedactedToolView(
            tool_input=_counting(tool_input),
            output=None if output is None else _counting(output),
        )

    monkeypatch.setattr(agent_module, "redact_sensitive", _counting)
    monkeypatch.setattr(agent_module, "redact_tool_view", _counting_view)

    # Collapse the surrounding machinery so only the loop's own logic runs.
    monkeypatch.setattr(agent_module, "get_tracker", _Tracker)
    monkeypatch.setattr(agent_module, "get_llm", lambda _role: _LLM())
    monkeypatch.setattr(agent_module, "get_available_tools", lambda _resolved: [])
    monkeypatch.setattr(agent_module, "select_investigation_tools", lambda _tools, _state: [])
    monkeypatch.setattr(
        agent_module,
        "build_connected_tool_context",
        lambda _r, _t: build_connected_tool_context({}, []),
    )
    monkeypatch.setattr(agent_module, "build_seed_calls", lambda _s, _t, _l: [])
    monkeypatch.setattr(agent_module, "build_investigation_system_prompt", lambda _s: "system")
    monkeypatch.setattr(agent_module, "format_alert_context", lambda _s, _t: "alert")
    monkeypatch.setattr(agent_module, "estimate_message_tokens", lambda *_a, **_k: 10)
    monkeypatch.setattr(agent_module, "system_and_tools_overhead", lambda *_a, **_k: 0)
    monkeypatch.setattr(agent_module, "context_budget_ceiling_for_model", lambda *_a, **_k: 100000)
    monkeypatch.setattr(agent_module, "enforce_context_budget", lambda messages, **_k: messages)
    monkeypatch.setattr(
        agent_module, "execute_tools", lambda calls, *_a: [{"hits": 1}] * len(calls)
    )
    monkeypatch.setattr(agent_module, "merge_tool_evidence", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_module, "tool_event_payload", lambda *_a, **_k: {})
    monkeypatch.setattr(agent_module, "tool_call_signature", lambda tc: tc.id)
    return walked


def test_loop_walks_each_tool_input_once(loop_harness: list[Any]) -> None:
    """One tool call through the real loop must redact its input exactly once."""
    # Arrange
    agent = ConnectedInvestigationAgent()
    state: dict[str, Any] = {"resolved_integrations": {}, "alert_name": "cpu"}

    # Act
    agent.run(state)  # type: ignore[arg-type]

    # Assert
    inputs_walked = [value for value in loop_harness if value is TOOL_INPUT]
    assert len(inputs_walked) == 1, (
        f"the loop redaction-walked one tool input {len(inputs_walked)} times"
    )

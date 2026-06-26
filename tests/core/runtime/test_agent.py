from __future__ import annotations

from typing import Any

from core.runtime import Agent, AgentLoopResult


def test_agent_owns_messages_and_reduces_tool_events(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_loop(**kwargs: Any) -> AgentLoopResult:
        captured.update(kwargs)
        on_event = kwargs["on_event"]
        on_event("tool_start", {"id": "call-1", "name": "lookup"})
        assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
        on_event("tool_end", {"id": "call-1", "name": "lookup", "output": {"ok": True}})
        kwargs["messages"].append({"role": "assistant", "content": "done"})
        return AgentLoopResult(messages=kwargs["messages"], final_text="done", executed=[])

    monkeypatch.setattr("core.runtime.agent_loop.run_agent_loop", _fake_loop)

    events: list[str] = []
    agent = Agent(
        llm=object(),
        system_prompt="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=2,
    )
    agent.subscribe(lambda kind, _data: events.append(kind))

    result = agent.prompt("hello")

    assert captured["system"] == "sys"
    assert result.final_text == "done"
    assert agent.messages[-1] == {"role": "assistant", "content": "done"}
    assert agent.pending_tool_calls == set()
    assert events == ["tool_start", "tool_end"]


def test_agent_rejects_reentrant_runs(monkeypatch: Any) -> None:
    agent = Agent(
        llm=object(),
        system_prompt="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=2,
    )
    agent.is_running = True

    try:
        agent.prompt("hello")
    except RuntimeError as exc:
        assert "already running" in str(exc)
    else:
        raise AssertionError("expected reentrant prompt to fail")
    assert agent.messages == []

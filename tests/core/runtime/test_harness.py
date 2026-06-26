from __future__ import annotations

from typing import Any

from core.runtime import AgentHarness, AgentLoopResult


def test_harness_builds_turn_state_and_persists_messages(monkeypatch: Any) -> None:
    saved: list[list[dict[str, Any]]] = []
    captured: dict[str, Any] = {}

    class Store:
        def load_messages(self) -> list[dict[str, Any]]:
            return [{"role": "system-note", "content": "existing"}]

        def save_messages(self, messages: list[dict[str, Any]]) -> None:
            saved.append(messages)

    def _fake_loop(**kwargs: Any) -> AgentLoopResult:
        captured.update(kwargs)
        kwargs["messages"].append({"role": "assistant", "content": "answer"})
        return AgentLoopResult(messages=kwargs["messages"], final_text="answer", executed=[])

    monkeypatch.setattr("core.runtime.agent_loop.run_agent_loop", _fake_loop)

    harness = AgentHarness(
        llm_factory=object,
        system_prompt=lambda context: f"resolved={sorted(context.resolved_integrations)}",
        tool_provider=lambda context: context.resolved_integrations["tools"],
        integration_provider=lambda: {"tools": []},
        max_iterations=3,
        store=Store(),
    )

    result = harness.prompt("question")

    assert captured["system"] == "resolved=['tools']"
    assert captured["max_iterations"] == 3
    assert captured["messages"][0] == {"role": "system-note", "content": "existing"}
    assert captured["messages"][1] == {"role": "user", "content": "question"}
    assert result.final_text == "answer"
    assert saved == [captured["messages"]]


def test_harness_forwards_agent_events(monkeypatch: Any) -> None:
    def _fake_loop(**kwargs: Any) -> AgentLoopResult:
        kwargs["on_event"]("agent_start", {"tool_count": 0})
        return AgentLoopResult(messages=kwargs["messages"], final_text="", executed=[])

    monkeypatch.setattr("core.runtime.agent_loop.run_agent_loop", _fake_loop)

    harness = AgentHarness(
        llm_factory=object,
        system_prompt="sys",
        tool_provider=lambda _context: [],
        integration_provider=lambda: {},
        max_iterations=1,
    )
    events: list[str] = []
    harness.subscribe(lambda kind, _data: events.append(kind))

    harness.prompt("question")

    assert events == ["agent_start"]

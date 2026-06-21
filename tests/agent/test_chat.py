from __future__ import annotations

from typing import Any, cast

from app.agent.chat import ChatAgent
from app.state import AgentState


class _FakeTool:
    name = "lookup_incident"

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "args": kwargs}


class _FakeLLM:
    def __init__(self) -> None:
        self.invocations: list[list[dict[str, Any]]] = []

    def invoke(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.invocations.append(list(messages))
        if len(self.invocations) == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "lookup_incident",
                        "args": {"service": "api"},
                    }
                ],
            }
        return {"content": "The API incident has supporting tool evidence."}


def test_tracer_data_chat_executes_tool_calls_and_finishes(monkeypatch: Any) -> None:
    fake_llm = _FakeLLM()

    monkeypatch.setattr("app.agent.chat._route", lambda _state: "tracer_data")
    monkeypatch.setattr("app.agent.chat._get_llm", lambda **_kwargs: fake_llm)
    monkeypatch.setattr("app.agent.chat.get_registered_tools", lambda _surface: [_FakeTool()])

    state = cast(AgentState, {"messages": [{"role": "user", "content": "check api"}]})
    result = ChatAgent().run(state)

    assert len(fake_llm.invocations) == 2
    assert result["messages"][0]["tool_calls"][0]["name"] == "lookup_incident"
    assert result["messages"][1]["role"] == "tool"
    assert result["messages"][2]["content"] == "The API incident has supporting tool evidence."
    assert any(message["role"] == "tool" for message in fake_llm.invocations[1])


def test_route_classification(monkeypatch: Any) -> None:
    from app.agent.chat import _route

    class FakeRouteLLM:
        def __init__(self, content: str) -> None:
            self.content = content
            self.invocations: list[Any] = []

        def invoke(self, messages: list[dict[str, Any]]) -> Any:
            self.invocations.append(messages)

            class FakeResponse:
                def __init__(self, content: str) -> None:
                    self.content = content

            return FakeResponse(self.content)

    # Test routing to tracer_data
    fake_llm_tracer = FakeRouteLLM("tracer_data")
    monkeypatch.setattr("app.agent.chat.get_llm_for_tools", lambda: fake_llm_tracer)
    state = cast(AgentState, {"messages": [{"role": "user", "content": "RDS latency spike"}]})
    assert _route(state) == "tracer_data"
    assert len(fake_llm_tracer.invocations) == 1
    assert "tracer_data" in fake_llm_tracer.invocations[0][0]["content"]

    # Test routing to general
    fake_llm_general = FakeRouteLLM("general")
    monkeypatch.setattr("app.agent.chat.get_llm_for_tools", lambda: fake_llm_general)
    state = cast(AgentState, {"messages": [{"role": "user", "content": "Explain RDS"}]})
    assert _route(state) == "general"

    # Test routing with invalid LLM response defaults to general
    fake_llm_invalid = FakeRouteLLM("something_else")
    monkeypatch.setattr("app.agent.chat.get_llm_for_tools", lambda: fake_llm_invalid)
    state = cast(AgentState, {"messages": [{"role": "user", "content": "Explain RDS"}]})
    assert _route(state) == "general"

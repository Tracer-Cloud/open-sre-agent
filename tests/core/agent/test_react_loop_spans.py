"""ReAct loop emits ``span_kind=llm`` around each model invoke."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.agent import Agent
from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from core.llm.types import AgentLLMResponse
from platform.observability.trace.spans import (
    NoopSessionTraceSink,
    bind_session_trace,
    set_session_trace_sink,
)
from surfaces.interactive_shell.session.trace_sink import JsonlSessionTraceSink


@pytest.fixture(autouse=True)
def _reset_session_trace_sink() -> Any:
    set_session_trace_sink(NoopSessionTraceSink())
    yield
    set_session_trace_sink(NoopSessionTraceSink())


class _NoToolLLM:
    model_id = "test-model"

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        return AgentLLMResponse(content="done", tool_calls=[], raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


def test_agent_run_emits_llm_span_when_sink_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-llm-span"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_sink(JsonlSessionTraceSink(storage=storage))

    agent = Agent(
        llm=_NoToolLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )
    with bind_session_trace(session_id):
        result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_text == "done"
    spans = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if json.loads(line).get("type") == "trace_span"
    ]
    llm_spans = [s for s in spans if s.get("span_kind") == "llm"]
    assert len(llm_spans) == 1
    assert llm_spans[0]["name"] == "test-model"
    assert llm_spans[0]["status"] == "ok"
    assert llm_spans[0]["attributes"]["iteration"] == 0
    assert "duration_ms" in llm_spans[0]


def test_agent_run_skips_llm_span_when_noop() -> None:
    agent = Agent(
        llm=_NoToolLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )
    result = agent.run([{"role": "user", "content": "hello"}])
    assert result.final_text == "done"

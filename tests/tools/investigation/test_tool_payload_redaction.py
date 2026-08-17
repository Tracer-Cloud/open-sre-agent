"""Seed evidence shares one redacted input across progress and state sinks."""

from __future__ import annotations

from typing import Any

import pytest

from core.llm.types import ToolCall
from tools.investigation.stages.gather_evidence import agent as agent_module
from tools.investigation.stages.gather_evidence.agent import ConnectedInvestigationAgent


class _NullTracker:
    def record_tool_start(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""

    def record_tool_end(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""


def test_seed_input_is_redacted_once_across_start_and_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walked: list[Any] = []
    real_redact = agent_module.redact_sensitive
    real_redact_view = agent_module.redact_tool_view

    def _counting(value: Any) -> Any:
        walked.append(value)
        return real_redact(value)

    def _counting_view(tool_input: Any, output: Any = None) -> Any:
        walked.append(tool_input)
        return real_redact_view(tool_input, output)

    monkeypatch.setattr(agent_module, "redact_sensitive", _counting)
    monkeypatch.setattr(agent_module, "redact_tool_view", _counting_view)
    agent = ConnectedInvestigationAgent()
    agent._tracker = _NullTracker()
    tool_input = {"query": "status:error", "api_key": "sk-secret"}
    tool_call = ToolCall(id="seed-1", name="query_logs", input=tool_input)

    redacted_input = agent._record_seed_start(tool_call)
    redacted = agent._record_seed_end(
        tool_call,
        {"hits": 3},
        redacted_input=redacted_input,
    )

    assert len([value for value in walked if value is tool_input]) == 1
    assert redacted.tool_input["api_key"] != "sk-secret"
    assert redacted.output == {"hits": 3}

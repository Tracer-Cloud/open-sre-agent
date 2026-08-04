"""A tool's input must be redacted once per call, not once per sink.

Redaction deep-copies the whole payload to strip credentials. The investigation
loop announces a tool call (redacting its input), then pairs the result with
that input when the output lands. Redacting a second time to build the pair
doubles the walk on every tool call — and tool inputs carry log queries and
result sets, so the tree is not small.

``RedactedToolView`` exists to be shared across sinks; these tests pin that the
loop actually shares it.
"""

from __future__ import annotations

from typing import Any

import pytest

from tools.investigation.stages.gather_evidence import agent as agent_module
from tools.investigation.stages.gather_evidence.agent import ConnectedInvestigationAgent


class _Call:
    """Minimal stand-in for a ``ToolCall``."""

    def __init__(self, call_id: str, tool_input: dict[str, Any]) -> None:
        self.id = call_id
        self.name = "query_logs"
        self.input = tool_input


class _NullTracker:
    def record_tool_start(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""

    def record_tool_end(self, *_args: object, **_kwargs: object) -> None:
        """Ignore progress."""


def _agent() -> ConnectedInvestigationAgent:
    instance = ConnectedInvestigationAgent.__new__(ConnectedInvestigationAgent)
    instance._tracker = _NullTracker()
    instance._redacted_inputs = {}
    instance._on_tuple_event = None
    instance._on_runtime_event = None
    return instance


@pytest.fixture
def counted_redactions(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every value handed to the top-level redaction walk."""
    walked: list[Any] = []
    real = agent_module.redact_sensitive

    def _counting(value: Any) -> Any:
        walked.append(value)
        return real(value)

    monkeypatch.setattr(agent_module, "redact_sensitive", _counting)
    monkeypatch.setattr(
        agent_module,
        "redact_tool_view",
        lambda tool_input, output=None: agent_module.RedactedToolView(
            tool_input=_counting(tool_input),
            output=None if output is None else _counting(output),
        ),
    )
    return walked


def test_tool_input_is_redacted_once_across_start_and_end(
    counted_redactions: list[Any],
) -> None:
    """Announcing the call and attaching its output must share one redaction."""
    # Arrange
    agent = _agent()
    tool_input = {"query": "status:error", "api_key": "sk-secret"}
    call = _Call("call-1", tool_input)

    # Act
    agent._record_tool_start(call)
    view = agent._redacted_view(call, output={"hits": 3})

    # Assert
    inputs_walked = [value for value in counted_redactions if value is tool_input]
    assert len(inputs_walked) == 1, (
        f"tool input was redaction-walked {len(inputs_walked)} times for one call"
    )
    assert view.tool_input["api_key"] != "sk-secret", "redaction must still apply"
    assert view.output == {"hits": 3}


def test_an_unannounced_call_still_gets_a_redacted_input() -> None:
    """Reuse must not become a hole: no cached input means redact it now."""
    # Arrange
    agent = _agent()
    call = _Call("call-unseen", {"query": "status:error", "password": "hunter2"})

    # Act — no _record_tool_start for this call.
    view = agent._redacted_view(call, output={"hits": 1})

    # Assert
    assert view.tool_input["password"] != "hunter2"


def test_each_call_consumes_only_its_own_cached_input() -> None:
    """Two in-flight calls must not swap payloads."""
    # Arrange
    agent = _agent()
    first = _Call("call-a", {"query": "alpha"})
    second = _Call("call-b", {"query": "beta"})

    # Act
    agent._record_tool_start(first)
    agent._record_tool_start(second)
    view_b = agent._redacted_view(second, output=None)
    view_a = agent._redacted_view(first, output=None)

    # Assert
    assert view_a.tool_input["query"] == "alpha"
    assert view_b.tool_input["query"] == "beta"
    assert agent._redacted_inputs == {}, "cached inputs must drain as calls complete"

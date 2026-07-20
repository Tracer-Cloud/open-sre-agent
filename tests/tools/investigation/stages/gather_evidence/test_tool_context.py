from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from core.domain.types.retrieval import RetrievalControls
from core.tool_framework.registered_tool import RegisteredTool
from tools.investigation.stages.gather_evidence.tools import build_connected_tool_context


def _tool(name: str, source: str) -> RegisteredTool:
    def _run(**_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    return RegisteredTool(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        source=source,  # type: ignore[arg-type]
        run=cast(Callable[..., Any], _run),
        use_cases=[],
        retrieval_controls=RetrievalControls(),
    )


def test_build_connected_tool_context_marks_membership_and_sorts() -> None:
    # Arrange: two configured integrations, an underscore-private key that must be
    # ignored, an empty (falsy) value that does not count as connected, and tools
    # from both a connected and a disconnected source.
    resolved = {
        "datadog": {"api_key": "x"},
        "grafana": {"url": "y"},
        "_secret": {"token": "z"},
        "empty": {},
    }
    tools = [
        _tool("dd_query", "datadog"),
        _tool("pd_list", "pagerduty"),  # disconnected source
        _tool("gf_logs", "grafana"),
    ]

    # Act
    ctx = build_connected_tool_context(resolved, tools)

    # Assert: connected list excludes the _-prefixed and empty keys, and is sorted.
    assert ctx["connected_integrations"] == ["datadog", "grafana"]

    # Assert: the connected flag is the membership decision R1 optimizes — a
    # configured source is connected, an unconfigured one is not.
    sources = ctx["available_sources"]
    assert sources["datadog"]["connected"] is True
    assert sources["grafana"]["connected"] is True
    assert sources["pagerduty"]["connected"] is False

    # Assert: tools grouped under their source; action names sorted.
    assert sources["datadog"]["tools"] == ["dd_query"]
    assert ctx["available_action_names"] == ["dd_query", "gf_logs", "pd_list"]

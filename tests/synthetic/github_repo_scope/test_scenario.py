"""Synthetic regression scenario for GitHub repository-scope enrichment.

The scenario drives the production headless turn and real action-tool registry.
Only the LLM decision and GitHub MCP transport are scripted, so a regression in
turn-plan enrichment makes ``list_github_commits`` disappear before planning.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any

from rich.console import Console

from core.agent_harness.runtime import DefaultToolProvider, InMemoryHeadlessBuild, TurnBinding
from core.agent_harness.turns.headless_adapters import BufferOutputSink, InMemorySessionState
from core.llm.types import AgentLLMResponse, ToolCall

_SCENARIO_PATH = Path(__file__).parent / "001-alert-repository-url" / "scenario.json"


class _ScriptedActionLlm:
    """Choose the expected GitHub tool, then conclude from its fixture result."""

    model_id = "synthetic-github-repo-scope"

    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.tool_schema_names: list[str] = []

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        self.tool_schema_names = [str(tool.name) for tool in tools]
        return [{"name": tool.name} for tool in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (messages, system, tools)
        return next(self._responses)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "input": call.input} for call in tool_calls
            ],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [
                {"id": call.id, "name": call.name, "output": result}
                for call, result in zip(tool_calls, results)
            ],
        }


def test_alert_repository_url_enables_github_tool_in_production_turn(
    monkeypatch: Any,
) -> None:
    """A message repo URL exposes and executes the GitHub tool through the real turn."""
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    expected = scenario["expected"]
    base_integrations = scenario["base_resolved_integrations"]
    transport_calls: list[tuple[str, dict[str, Any]]] = []

    def _fixture_github_mcp_call(
        _config: Any,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        transport_calls.append((tool_name, arguments))
        commits = [{"sha": expected["commit_sha"], "message": "Synthetic scope regression"}]
        return {
            "is_error": False,
            "tool": tool_name,
            "arguments": arguments,
            "structured_content": commits,
            "text": json.dumps(commits),
            "content": [],
        }

    monkeypatch.setattr(
        "integrations.github.tools.commits.call_github_mcp_tool",
        _fixture_github_mcp_call,
    )

    tool_call = ToolCall(
        id="github-commits-5782",
        name=expected["tool_name"],
        input={"owner": expected["owner"], "repo": expected["repo"]},
    )
    llm = _ScriptedActionLlm(
        iter(
            [
                AgentLLMResponse(content="", tool_calls=[tool_call]),
                AgentLLMResponse(
                    content=f"The latest relevant commit is {expected['commit_sha']}.",
                ),
                AgentLLMResponse(content='{"verdict":"GOAL_REACHED"}'),
            ]
        )
    )

    session = InMemorySessionState(
        configured_integrations=["github"],
        configured_integrations_known=True,
        resolved_integrations_cache=base_integrations,
    )
    output = BufferOutputSink()
    tools = DefaultToolProvider(
        session,
        Console(file=StringIO(), force_terminal=False, highlight=False),
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=tools,
        llm_factory=lambda: llm,
    )

    result = agent.handle(scenario["user_message"], TurnBinding())

    assert expected["tool_name"] in llm.tool_schema_names
    assert transport_calls == [
        (
            "list_commits",
            {"owner": expected["owner"], "repo": expected["repo"], "perPage": 10},
        )
    ]
    assert expected["commit_sha"] in result.assistant_response_text
    assert session.vcs_repo_scopes["github"] == (expected["owner"], expected["repo"])
    assert "owner" not in session.resolved_integrations_cache["github"]
    assert "repo" not in session.resolved_integrations_cache["github"]

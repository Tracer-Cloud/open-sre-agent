"""Gather discovery budget — block MCP schema thrash (S1 parity gap).

Live symptom (session e4b6f56b…): one Windows-users metric ask spent the
gather turn on ``list_posthog_tools`` + many ``call_posthog_tool`` exec
``search`` / ``info`` / ``schema`` / ``read-data-schema`` calls and never
ran a count query. Circuit breaker only trips on connectivity errors, so
successful discovery loops forever.

This guard caps discovery-style MCP bridge calls (``list_*_tools`` /
``call_*_tool``) per gather turn and dedupes exact fingerprints without
``json.dumps``.
"""

from __future__ import annotations

from core.agent_harness.turns.gather_discovery_budget import (
    DEFAULT_MAX_DISCOVERY_CALLS,
    is_mcp_exec_bridge,
    is_mcp_list_tools,
    with_gather_discovery_budget,
)
from core.execution import ToolExecutionRequest, ToolExecutionResult
from core.llm.types import ToolCall


def _request(
    tool: str,
    arguments: dict,
    *,
    source: str = "posthog_mcp",
) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_call=ToolCall(id="tc", name=tool, input=arguments),
        tool=type("T", (), {"source": source})(),  # type: ignore[arg-type]
        arguments=arguments,
        source=source,
        resolved_integrations={},
    )


def _ok() -> ToolExecutionResult:
    return ToolExecutionResult(content='{"ok": true}', is_error=False)


def test_s1_style_discovery_burst_is_capped() -> None:
    """Red on the S1 thrash pattern: > budget discovery calls in one gather turn."""
    hooks = with_gather_discovery_budget()
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None

    # First list + three schema probes are allowed (within default budget).
    allowed = [
        _request("list_posthog_tools", {"name_filter": "exec", "include_schema": True}),
        _request(
            "call_posthog_tool",
            {
                "name": "exec",
                "arguments": {
                    "command": "search read-data-schema",
                    "context": "locate schema tool",
                },
            },
        ),
        _request(
            "call_posthog_tool",
            {
                "name": "exec",
                "arguments": {
                    "command": "info query-trends",
                    "context": "inspect trends",
                },
            },
        ),
        _request(
            "call_posthog_tool",
            {
                "name": "exec",
                "arguments": {
                    "command": "schema query-trends series",
                    "context": "series schema",
                },
            },
        ),
    ]
    for req in allowed:
        assert hooks.before_tool_call(req) is None
        hooks.after_tool_call(req, _ok())

    # Next discovery call (S1 kept going to 10–15) must be blocked.
    overflow = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {
                "command": "schema query-trends dateRange",
                "context": "date range schema",
            },
        },
    )
    decision = hooks.before_tool_call(overflow)
    assert decision is not None
    assert decision.blocked
    assert "discovery" in decision.reason.lower()
    assert str(DEFAULT_MAX_DISCOVERY_CALLS) in decision.reason


def test_exact_duplicate_discovery_blocked_even_under_budget() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=8)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    req = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {"command": "search query-trends", "context": "a"},
        },
    )
    assert hooks.before_tool_call(req) is None
    hooks.after_tool_call(req, _ok())
    # Same command, different context prose — still a duplicate discovery.
    dup = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {"command": "search query-trends", "context": "b"},
        },
    )
    decision = hooks.before_tool_call(dup)
    assert decision is not None
    assert decision.blocked
    assert "already" in decision.reason.lower()


def test_real_query_call_still_allowed_after_discovery_budget() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=1)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    list_req = _request("list_posthog_tools", {"name_filter": "exec"})
    assert hooks.before_tool_call(list_req) is None
    hooks.after_tool_call(list_req, _ok())

    query = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {
                "command": (
                    'call query-trends {"dateRange":{"date_from":"-7d"},'
                    '"series":[{"event":"cli_invoked","math":"dau"}]}'
                ),
                "context": "count Windows users",
            },
        },
    )
    assert hooks.before_tool_call(query) is None


def test_mcp_bridge_naming_matches_any_vendor_not_just_posthog() -> None:
    assert is_mcp_list_tools("list_acme_tools")
    assert is_mcp_exec_bridge("call_acme_tool")
    assert not is_mcp_list_tools("list_tools")
    assert not is_mcp_exec_bridge("call_tool")
    assert not is_mcp_list_tools("query_grafana_logs")


def test_non_posthog_mcp_bridge_discovery_is_capped() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=1)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    first = _request(
        "list_acme_tools",
        {"name_filter": "exec"},
        source="acme_mcp",
    )
    assert hooks.before_tool_call(first) is None
    hooks.after_tool_call(first, _ok())
    overflow = _request(
        "call_acme_tool",
        {"name": "exec", "arguments": {"command": "schema query-metrics"}},
        source="acme_mcp",
    )
    decision = hooks.before_tool_call(overflow)
    assert decision is not None and decision.blocked

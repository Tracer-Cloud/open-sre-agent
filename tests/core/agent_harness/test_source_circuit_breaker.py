"""Tests for ``core.agent_harness.turns.source_circuit_breaker``.

The breaker must skip further calls to a source only after a transport-level
failure, leave application errors and healthy sources alone, and steer the
model to other sources through the block reason.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.source_circuit_breaker import SourceCircuitBreaker
from core.execution import (
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
)
from core.llm.types import ToolCall

_TIMEOUT_MARKER = "Connection to 172.29.99.99 timed out. (connect timeout=10)"
_APP_ERROR_MARKER = "Mimir datasource not found"


def _request(source: str, tool_name: str = "query_metrics") -> ToolExecutionRequest:
    tool = type("T", (), {"source": source})()
    return ToolExecutionRequest(
        tool_call=ToolCall(id="tc-1", name=tool_name, input={}),
        tool=tool,  # type: ignore[arg-type]
        arguments={},
        source=source,
        resolved_integrations={},
    )


def _error_result(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(content=message, is_error=True)


def test_connectivity_error_skips_later_calls_to_that_source() -> None:
    # Arrange: one grafana call fails at the transport level.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana"), _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}")
    )

    # Act: the model asks for another grafana tool in a later iteration.
    decision = hooks.before_tool_call(_request("grafana", tool_name="query_grafana_logs"))

    # Assert: the call is blocked without running, and the reason steers away.
    assert decision is not None
    assert decision.blocked
    assert "grafana" in decision.reason
    assert "unreachable" in decision.reason
    assert "different connected source" in decision.reason
    assert _TIMEOUT_MARKER in decision.reason


def test_application_error_does_not_trip_the_breaker() -> None:
    # Arrange: grafana is reachable but a datasource is missing.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(_request("grafana"), _error_result(_APP_ERROR_MARKER))

    # Act + Assert: the next grafana call still runs.
    assert hooks.before_tool_call(_request("grafana")) is None


def test_success_result_does_not_trip_the_breaker() -> None:
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None

    hooks.after_tool_call(
        _request("posthog"), ToolExecutionResult(content="events: []", is_error=False)
    )

    assert hooks.before_tool_call(_request("posthog")) is None


def test_other_sources_stay_callable_after_one_source_goes_down() -> None:
    # Arrange: kubernetes is down.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("kubernetes"), _error_result("Connection refused to 127.0.0.1:50859")
    )

    # Act + Assert: kubernetes is skipped, sentry is not.
    kubernetes_decision = hooks.before_tool_call(_request("kubernetes"))
    assert kubernetes_decision is not None and kubernetes_decision.blocked
    assert hooks.before_tool_call(_request("sentry")) is None


def test_unknown_source_is_never_marked_down() -> None:
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None

    hooks.after_tool_call(_request("unknown"), _error_result(f"boom: {_TIMEOUT_MARKER}"))

    assert hooks.before_tool_call(_request("unknown")) is None


def test_prior_success_limits_breaker_to_failing_tool() -> None:
    # Arrange: grafana already answered once this turn; a later tool then times out.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        ToolExecutionResult(content="cpu=0.2", is_error=False),
    )
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_logs"),
        _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
    )

    # Act + Assert: only the failing tool is skipped; other grafana tools still run.
    logs = hooks.before_tool_call(_request("grafana", tool_name="query_grafana_logs"))
    assert logs is not None and logs.blocked
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_alerts")) is None


def test_downstream_connection_error_does_not_poison_vendor() -> None:
    # Arrange: grafana is up but reports that a datasource backend is down.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana"),
        _error_result("datasource prometheus: connection refused to 10.0.0.5:9090"),
    )

    # Act + Assert: other grafana tools still run — the vendor itself is reachable.
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_logs")) is None


class _ConnectTimeoutTool:
    """Registered-tool fake whose run always fails like a dead host."""

    name = "query_grafana_metrics"
    source = "grafana"
    parallel_safe = False

    def validate_public_input(self, _payload: dict[str, Any]) -> str:
        return ""

    def extract_params(self, _sources: dict[str, Any]) -> dict[str, Any]:
        return {}

    def run(self, **_kwargs: Any) -> dict[str, Any]:
        return {"error": f"HTTPConnectionPool: {_TIMEOUT_MARKER}"}


def test_execute_tool_calls_blocks_second_call_through_real_seam() -> None:
    # Arrange: two sequential calls to the same dead-host tool.
    tool = _ConnectTimeoutTool()
    hooks: ToolExecutionHooks = SourceCircuitBreaker().hooks()
    first_call = [ToolCall(id="tc-1", name=tool.name, input={})]
    second_call = [ToolCall(id="tc-2", name=tool.name, input={})]

    # Act: the first call runs (and times out); the second is short-circuited.
    first = execute_tool_calls(first_call, [tool], {}, hooks=hooks)  # type: ignore[list-item]
    second = execute_tool_calls(second_call, [tool], {}, hooks=hooks)  # type: ignore[list-item]

    # Assert: the first result carries the real failure; the second never ran
    # the tool and tells the model to pivot.
    assert first[0].is_error
    assert _TIMEOUT_MARKER in str(first[0].content)
    assert second[0].is_error
    assert "skipped" in str(second[0].content)
    assert "grafana" in str(second[0].content)
    assert second[0].metadata.get("skipped_source") == "grafana"

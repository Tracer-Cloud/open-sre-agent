"""Tests for ``core.agent_harness.turns.source_circuit_breaker``.

The breaker must skip further calls to a tool only after a transport-level
failure, leave application errors and sibling tools alone, and steer the
model through the block reason. Marks are tool-scoped so a concurrent success
on the same vendor cannot race a source-wide poison.
"""

from __future__ import annotations

import threading
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


def test_connectivity_error_skips_later_calls_to_that_tool() -> None:
    # Arrange: one grafana tool fails at the transport level.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
    )

    # Act: the model retries the same tool in a later iteration.
    decision = hooks.before_tool_call(_request("grafana", tool_name="query_grafana_metrics"))

    # Assert: the call is blocked without running, and the reason steers away.
    assert decision is not None
    assert decision.blocked
    assert "query_grafana_metrics" in decision.reason
    assert "unreachable" in decision.reason
    assert _TIMEOUT_MARKER in decision.reason


def test_connectivity_error_does_not_skip_sibling_tools_on_same_source() -> None:
    # Arrange: metrics timed out; logs is a different endpoint on grafana.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
    )

    # Act + Assert: sibling tools still run — first failure must not poison the vendor.
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_logs")) is None
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_alerts")) is None


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


def test_other_sources_stay_callable_after_one_tool_goes_down() -> None:
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("kubernetes", tool_name="k8s_get_pods"),
        _error_result("Connection refused to 127.0.0.1:50859"),
    )

    k8s = hooks.before_tool_call(_request("kubernetes", tool_name="k8s_get_pods"))
    assert k8s is not None and k8s.blocked
    assert hooks.before_tool_call(_request("sentry", tool_name="sentry_issues")) is None


def test_unknown_source_is_never_marked_down() -> None:
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None

    hooks.after_tool_call(_request("unknown"), _error_result(f"boom: {_TIMEOUT_MARKER}"))

    assert hooks.before_tool_call(_request("unknown")) is None


def test_success_clears_prior_mark_for_that_tool_only() -> None:
    # Arrange: logs timed out, then metrics succeeds, then logs would retry.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_logs"),
        _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
    )
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        ToolExecutionResult(content="cpu=0.2", is_error=False),
    )

    # Act + Assert: success on metrics does not clear the logs mark; alerts stay open.
    logs = hooks.before_tool_call(_request("grafana", tool_name="query_grafana_logs"))
    assert logs is not None and logs.blocked
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_alerts")) is None


def test_same_tool_success_after_failure_clears_mark() -> None:
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
    )
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        ToolExecutionResult(content="cpu=0.2", is_error=False),
    )

    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_metrics")) is None
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_logs")) is None


def test_success_then_failure_does_not_repoison_same_tool() -> None:
    # Arrange: tool answered, then a concurrent/late connectivity error arrives.
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        ToolExecutionResult(content="cpu=0.2", is_error=False),
    )
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
    )

    # Act + Assert: sticky success wins — next iteration still runs the tool.
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_metrics")) is None


def test_concurrent_same_tool_success_then_failure_does_not_repoison() -> None:
    """Overlapping same-tool success + failure must leave the tool callable.

    If success clears the mark and failure's setdefault recreates it, the next
    gather iteration blocks a tool that just returned evidence.
    """
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None

    barrier = threading.Barrier(2)
    errors: list[Exception] = []
    tool = "query_grafana_metrics"

    def _succeed() -> None:
        try:
            barrier.wait(timeout=2)
            hooks.after_tool_call(
                _request("grafana", tool_name=tool),
                ToolExecutionResult(content="cpu=0.2", is_error=False),
            )
        except Exception as exc:
            errors.append(exc)

    def _fail() -> None:
        try:
            barrier.wait(timeout=2)
            hooks.after_tool_call(
                _request("grafana", tool_name=tool),
                _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_succeed), threading.Thread(target=_fail)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert not errors
    assert hooks.before_tool_call(_request("grafana", tool_name=tool)) is None


def test_concurrent_failure_after_success_does_not_poison_siblings() -> None:
    """Failure completing after a sibling success must stay tool-scoped.

    Reproduces the old demotion race: source-wide setdefault after ``source_ok``
    could re-block the whole vendor.
    """
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _succeed() -> None:
        try:
            barrier.wait(timeout=2)
            hooks.after_tool_call(
                _request("grafana", tool_name="query_grafana_metrics"),
                ToolExecutionResult(content="cpu=0.2", is_error=False),
            )
        except Exception as exc:
            errors.append(exc)

    def _fail() -> None:
        try:
            barrier.wait(timeout=2)
            hooks.after_tool_call(
                _request("grafana", tool_name="query_grafana_logs"),
                _error_result(f"Max retries exceeded: {_TIMEOUT_MARKER}"),
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_succeed), threading.Thread(target=_fail)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert not errors

    logs = hooks.before_tool_call(_request("grafana", tool_name="query_grafana_logs"))
    assert logs is not None and logs.blocked
    # Sibling must remain callable — source-wide poison is the regression.
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_alerts")) is None
    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_metrics")) is None


def test_downstream_connection_error_does_not_poison_tool() -> None:
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.after_tool_call is not None
    assert hooks.before_tool_call is not None
    hooks.after_tool_call(
        _request("grafana", tool_name="query_grafana_metrics"),
        _error_result("datasource prometheus: connection refused to 10.0.0.5:9090"),
    )

    assert hooks.before_tool_call(_request("grafana", tool_name="query_grafana_metrics")) is None
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

    first = execute_tool_calls(first_call, [tool], {}, hooks=hooks)  # type: ignore[list-item]
    second = execute_tool_calls(second_call, [tool], {}, hooks=hooks)  # type: ignore[list-item]

    assert first[0].is_error
    assert _TIMEOUT_MARKER in str(first[0].content)
    assert second[0].is_error
    assert "skipped" in str(second[0].content)
    assert "query_grafana_metrics" in str(second[0].content)
    assert second[0].metadata.get("skipped_tool") == "query_grafana_metrics"
    assert second[0].metadata.get("skipped_source") == "grafana"

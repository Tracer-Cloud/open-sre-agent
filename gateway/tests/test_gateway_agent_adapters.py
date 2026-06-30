from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.agent_harness.session import ReplSession
from core.types import AgentToolContext
from gateway.agent.gateway_action_tools import (
    GATEWAY_RESOURCE_KEY,
    GatewayToolContext,
    execute_shell_tool,
    gateway_action_tools,
    normalize_investigation_alert_text,
    run_shell,
)
from gateway.agent.gateway_agent_adapters import (
    GatewayErrorReporter,
    GatewayPromptContextProvider,
    GatewayRunRecordFactory,
    GatewayToolProvider,
)
from gateway.agent.gateway_output_sink import GatewayOutputSink


def test_normalize_investigation_alert_text_strips_quotes() -> None:
    assert normalize_investigation_alert_text('"hello world"') == "hello world"


def test_gateway_action_tools_expose_shell_and_investigation() -> None:
    names = {tool.name for tool in gateway_action_tools()}
    assert names == {"shell_run", "investigation_start"}


def test_gateway_prompt_context_provider_reads_grounding() -> None:
    session = ReplSession()
    session.grounding.cli.build_text = MagicMock(return_value="CLI help")  # type: ignore[method-assign]
    session.grounding.agents_md.build_text = MagicMock(return_value="AGENTS")  # type: ignore[method-assign]
    session.grounding.log_cache_diagnostics = MagicMock()  # type: ignore[method-assign]

    provider = GatewayPromptContextProvider(session)

    assert provider.cli_reference() == "CLI help"
    assert provider.agents_md() == "AGENTS"
    assert provider.investigation_flow()
    provider.log_diagnostics("test")
    session.grounding.log_cache_diagnostics.assert_called_once_with("test")  # type: ignore[attr-defined]


def test_gateway_run_record_factory_records_token_usage() -> None:
    session = ReplSession()
    factory = GatewayRunRecordFactory(session)

    record = factory.build(
        client=MagicMock(),
        prompt="hello " * 20,
        response_text="world " * 20,
        started=0.0,
    )

    assert record.response_text.startswith("world")
    assert session.token_usage.get("input_estimated", 0) > 0
    assert session.token_usage.get("output_estimated", 0) > 0


def test_gateway_tool_provider_returns_action_tools_and_resources() -> None:
    session = ReplSession()
    sink = MagicMock(spec=GatewayOutputSink)
    provider = GatewayToolProvider(
        session=session,
        sink=sink,
        chat_id="42",
        logger=logging.getLogger("gateway.tests"),
    )

    tools = provider.action_tools(confirm_fn=lambda _p: "yes", is_tty=False)
    resources = provider.tool_resources()

    assert {tool.name for tool in tools} == {"shell_run", "investigation_start"}
    assert GATEWAY_RESOURCE_KEY in resources
    assert isinstance(resources[GATEWAY_RESOURCE_KEY], GatewayToolContext)


def test_gateway_tool_provider_observer_updates_sink() -> None:
    session = ReplSession()
    sink = MagicMock(spec=GatewayOutputSink)
    provider = GatewayToolProvider(
        session=session,
        sink=sink,
        chat_id="42",
        logger=logging.getLogger("gateway.tests"),
    )

    observer = provider.observer(message="run shell")
    observer("tool_start", {"name": "shell_run", "input": {"command": "pwd"}})

    sink.set_tool_status.assert_called_once_with("Running shell_run…")


@patch("gateway.agent.gateway_action_tools.execute_shell_command")
def test_execute_shell_tool_records_response_text(mock_execute: MagicMock) -> None:
    from tools.interactive_shell.shell.execution import ShellExecutionResult

    mock_execute.return_value = ShellExecutionResult(
        command="pwd",
        argv=["pwd"],
        stdout="/tmp\n",
        stderr="",
        exit_code=0,
        timed_out=False,
        truncated=False,
        executed_with_shell=False,
    )
    session = ReplSession()
    sink = MagicMock(spec=GatewayOutputSink)
    ctx = GatewayToolContext(session=session, sink=sink, chat_id="42")

    ok = execute_shell_tool({"command": "pwd"}, ctx)

    assert ok is True
    assert session.history[-1]["response_text"] == "/tmp"


def test_run_shell_requires_gateway_runtime_context() -> None:
    context = AgentToolContext(resolved_integrations={}, resources={})

    with pytest.raises(RuntimeError, match="gateway runtime context"):
        run_shell(command="pwd", context=context)


def test_gateway_error_reporter_logs_expected_errors_at_debug() -> None:
    test_logger = logging.getLogger("gateway.tests.error_reporter")
    reporter = GatewayErrorReporter(test_logger)

    with patch.object(test_logger, "debug") as mock_debug:
        reporter.report(ValueError("boom"), context="gateway.test", expected=True)

    mock_debug.assert_called_once()

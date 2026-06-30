from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult
from gateway.agent.dispatch_gateway_msg_to_agent import dispatch_gateway_msg_to_agent


@patch("gateway.agent.error_handling.report_exception")
@patch("gateway.agent.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
def test_dispatch_gateway_msg_to_agent_reports_exception_and_renders_error(
    mock_turn: MagicMock,
    mock_report: MagicMock,
) -> None:
    mock_turn.side_effect = RuntimeError("boom")
    session = MagicMock()
    session.session_id = "session-1"
    sink = MagicMock()
    test_logger = logging.getLogger("gateway.tests")

    result = dispatch_gateway_msg_to_agent(
        text="hi",
        session=session,
        chat_id="42",
        sink=sink,
        logger=test_logger,
    )

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["tags"]["gateway.turn_outcome"] == "exception"
    sink.render_error.assert_called_once()
    assert result.final_intent == "gateway_error"


@patch("gateway.agent.error_handling.report_exception")
@patch("gateway.agent.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
def test_dispatch_gateway_msg_to_agent_reports_empty_response(
    mock_turn: MagicMock,
    mock_report: MagicMock,
) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="gather_and_answer",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False),
    )
    session = MagicMock()
    session.session_id = "session-1"
    sink = MagicMock()
    test_logger = logging.getLogger("gateway.tests")

    result = dispatch_gateway_msg_to_agent(
        text="hi",
        session=session,
        chat_id="42",
        sink=sink,
        logger=test_logger,
    )

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["tags"]["gateway.turn_outcome"] == "empty_response"
    sink.render_error.assert_called_once()
    assert result.final_intent == "gather_and_answer"


@patch("gateway.agent.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
@patch("gateway.agent.dispatch_gateway_msg_to_agent._gateway_reasoning_provider")
def test_dispatch_gateway_msg_to_agent_passes_sink_hooks_and_reasoning(
    mock_reasoning: MagicMock,
    mock_turn: MagicMock,
) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="gather_and_answer",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False),
        assistant_response_text="hello",
        llm_run=MagicMock(response_text="hello"),
    )
    session = MagicMock()
    session.resolved_integrations_cache = {"github": {"token": "x"}}
    sink = MagicMock()
    mock_reasoning.return_value = MagicMock()

    dispatch_gateway_msg_to_agent(
        text="hi",
        session=session,
        chat_id="42",
        sink=sink,
        logger=logging.getLogger("gateway.tests"),
    )

    session.warm_resolved_integrations.assert_not_called()
    assert session.resolved_integrations_cache["_gateway_chat_id"] == "42"
    assert session.resolved_integrations_cache["github"] == {"token": "x"}
    kwargs = mock_turn.call_args.kwargs
    assert mock_turn.call_args.args == ("hi",)
    assert kwargs["session"] is session
    assert kwargs["is_tty"] is False
    assert kwargs["output"] is sink
    assert kwargs["reasoning"] is mock_reasoning.return_value
    assert kwargs["gather_enabled"] is True
    assert kwargs.get("tool_hooks") is None
    assert kwargs["prompts"] is not None
    assert kwargs["tools"] is not None
    assert kwargs["run_factory"] is not None
    assert kwargs["error_reporter"] is not None
    mock_reasoning.assert_called_once()


@patch("gateway.agent.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
def test_dispatch_gateway_msg_to_agent_finalizes_unanswered_action_response(
    mock_turn: MagicMock,
) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            1,
            1,
            1,
            False,
            True,
            response_text="OpenSRE Health",
        ),
    )
    session = MagicMock()
    sink = MagicMock()

    dispatch_gateway_msg_to_agent(
        text="/health",
        session=session,
        chat_id="42",
        sink=sink,
        logger=logging.getLogger("gateway.tests"),
    )

    sink.finalize.assert_called_once_with("OpenSRE Health")

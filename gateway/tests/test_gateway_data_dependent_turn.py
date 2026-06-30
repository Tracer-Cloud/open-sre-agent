"""End-to-end gateway turn test for data-dependent compound requests.

Regression coverage for the "check the weather and then send it to Slack" class
of request: the value produced by the first tool (``shell_run``) must reach the
model so the second tool (``slack_send_message``) can send the real result, not
a placeholder. This drives the real action tool-calling loop with the actual
``GatewayToolProvider`` and a scripted LLM that genuinely reads the prior tool
result before composing the Slack message.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent_harness.action_agent import ToolCallingDeps, run_agent_turn
from core.agent_harness.session import ReplSession
from core.llm.types import AgentLLMResponse, ToolCall
from gateway.agent.gateway_agent_adapters import GatewayToolProvider
from gateway.agent.gateway_output_sink import GatewayOutputSink
from tools.registry import clear_tool_registry_cache

_TEMPERATURE_LINE = "Antarctica: -40C"
_SLACK_WEBHOOK = "https://hooks.slack.test/abc"


class _SequentialWeatherToSlackLLM:
    """Scripted LLM that runs a lookup, observes its output, then sends it.

    Turn 1 emits ``shell_run``. Turn 2 only fires once the shell output has come
    back in the transcript — it extracts that real output and puts it into the
    ``slack_send_message`` call. Turn 3 concludes with no tool call. This mirrors
    a real model handling a data-dependent compound request sequentially.
    """

    def __init__(self) -> None:
        self.slack_message: str | None = None

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        shell_output = self._shell_output(messages)
        if shell_output is None:
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_shell",
                        name="shell_run",
                        input={"command": f"echo '{_TEMPERATURE_LINE}'"},
                    )
                ],
            )
        if self.slack_message is None:
            self.slack_message = shell_output
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_slack",
                        name="slack_send_message",
                        input={"message": f"Weather report: {shell_output}"},
                    )
                ],
            )
        return AgentLLMResponse(content="Done — sent the weather to Slack.")

    @staticmethod
    def _shell_output(messages: list[dict[str, Any]]) -> str | None:
        """Pull the ``shell_run`` output back out of the tool-result transcript."""
        for message in messages:
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            try:
                entries = json.loads(content) if isinstance(content, str) else content
            except (TypeError, ValueError):
                continue
            for entry in entries or []:
                if not isinstance(entry, dict) or entry.get("name") != "shell_run":
                    continue
                result = entry.get("result")
                try:
                    payload = json.loads(result) if isinstance(result, str) else result
                except (TypeError, ValueError):
                    continue
                if isinstance(payload, dict) and payload.get("output"):
                    return str(payload["output"])
        return None

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.input} for tc in tool_calls
            ],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "content": json.dumps(
                [
                    {"id": tc.id, "name": tc.name, "result": result}
                    for tc, result in zip(tool_calls, results)
                ],
                default=str,
            ),
        }


def test_gateway_turn_passes_shell_output_into_slack_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_tool_registry_cache()
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _SLACK_WEBHOOK)

    delivered: dict[str, str] = {}

    def _capture_send(message: str, *, webhook_url: str = "") -> tuple[bool, str]:
        delivered["message"] = message
        delivered["webhook_url"] = webhook_url
        return True, ""

    monkeypatch.setattr(
        "tools.slack_send_message_tool.delivery.send_slack_webhook_message",
        _capture_send,
    )

    session = ReplSession()
    session.resolved_integrations_cache = {"slack": {"webhook_url": _SLACK_WEBHOOK}}
    sink = MagicMock(spec=GatewayOutputSink)
    provider = GatewayToolProvider(
        session=session,
        sink=sink,
        chat_id="42",
        logger=logging.getLogger("gateway.tests"),
    )
    llm = _SequentialWeatherToSlackLLM()

    result = run_agent_turn(
        "check the weather in Antarctica and then send it to slack",
        session,
        output=MagicMock(spec=GatewayOutputSink),
        tools=provider,
        deps=ToolCallingDeps(llm_factory=lambda: llm),
    )

    # The lookup ran and the model composed the Slack message from its real output.
    assert llm.slack_message == _TEMPERATURE_LINE
    # The Slack tool actually delivered the temperature, not a placeholder.
    assert "message" in delivered
    assert _TEMPERATURE_LINE in delivered["message"]
    assert delivered["webhook_url"] == _SLACK_WEBHOOK
    # Both tool calls were planned and executed successfully this turn.
    assert result.handled is True
    assert result.executed_success_count >= 2

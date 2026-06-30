'''
Description:
--------------------------------
We want to have a very specific tests that validates wether the agent is working or not.
The test goes like this:
- We start the gateway and get the agent
- We send a message to the agent: "send a message to slack with the temperature in antartica, compute the temperature first and then send the message"
- We expect the agent to produce two or three turns (1: create temperature, 2: send message via slack that includes the temperature)
'''

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent_harness.action_agent import ToolCallingDeps, run_agent_turn
from core.agent_harness.session import ReplSession
from core.agent_harness.session.storage.memory import InMemorySessionStorage
from core.llm.types import AgentLLMResponse, ToolCall
from gateway.agent.gateway_agent_adapters import GatewayToolProvider
from gateway.agent.gateway_output_sink import GatewayOutputSink
from tools.registry import clear_tool_registry_cache

_USER_MESSAGE = (
    "send a message to slack with the temperature in antartica, "
    "compute the temperature first and then send the message"
)
# Genuinely computed by the first (shell) turn rather than hardcoded into the
# Slack call, so the test proves the second turn consumes the first turn's output.
_FREEZING_C = -20
_WIND_CHILL_C = -40
_EXPECTED_TEMPERATURE = f"Antarctica: {_FREEZING_C + _WIND_CHILL_C}C"
_COMPUTE_COMMAND = f"echo 'Antarctica:' $(({_FREEZING_C} + {_WIND_CHILL_C}))'C'"
_SLACK_WEBHOOK = "https://hooks.slack.test/abc"


class _ComputeThenSlackLLM:
    """Scripted LLM that computes a value, observes it, then sends it to Slack.

    This mirrors a real model handling a data-dependent compound request
    sequentially over multiple turns:

    * Turn 1 emits ``shell_run`` to compute the temperature.
    * Turn 2 fires only once the shell output is in the transcript; it extracts
      that real output and embeds it in the ``slack_send_message`` call.
    * Turn 3 concludes with a plain reply and no tool call.
    """

    def __init__(self) -> None:
        self.turns = 0
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
        self.turns += 1
        shell_output = self._shell_output(messages)
        if shell_output is None:
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_compute",
                        name="shell_run",
                        input={"command": _COMPUTE_COMMAND},
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
                        input={"message": f"Current temperature — {shell_output}"},
                    )
                ],
            )
        return AgentLLMResponse(content="Done — sent the Antarctica temperature to Slack.")

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
                    return str(payload["output"]).strip()
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


def test_agent_computes_temperature_then_sends_it_to_slack(
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

    # Start a gateway-style session and the gateway's own agent tool surface.
    session = ReplSession(storage=InMemorySessionStorage())
    session.resolved_integrations_cache = {"slack": {"webhook_url": _SLACK_WEBHOOK}}
    sink = MagicMock(spec=GatewayOutputSink)
    provider = GatewayToolProvider(
        session=session,
        sink=sink,
        chat_id="42",
        logger=logging.getLogger("gateway.tests.antartica"),
    )
    llm = _ComputeThenSlackLLM()

    result = run_agent_turn(
        _USER_MESSAGE,
        session,
        output=MagicMock(spec=GatewayOutputSink),
        tools=provider,
        deps=ToolCallingDeps(llm_factory=lambda: llm),
    )

    # The agent ran the compound request as a sequence of turns: compute, send,
    # finalize. "Two or three turns" — the final no-tool reply is the third.
    assert llm.turns == 3

    # Turn 1 computed the temperature; turn 2 read that real value (not a
    # placeholder) and used it to compose the Slack message.
    assert llm.slack_message == _EXPECTED_TEMPERATURE

    # The Slack tool actually delivered the computed temperature.
    assert _EXPECTED_TEMPERATURE in delivered.get("message", "")
    assert delivered["webhook_url"] == _SLACK_WEBHOOK

    # Both tool calls (shell_run + slack_send_message) were planned and succeeded.
    assert result.handled is True
    assert result.executed_success_count >= 2

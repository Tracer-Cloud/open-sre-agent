"""Gateway turn handler: dispatch one inbound message to the agent.

Transport-agnostic — it takes ``(text, session, sink, logger)`` and drives the
shared headless dispatch, then finalizes any outbound text on the sink. It knows
nothing about Telegram (or any specific transport); the composition root builds
one of these and hands it to whichever poller runs.
"""

from __future__ import annotations

import logging

from rich.console import Console

from config.gateway_output_sink import GatewayOutputSink
from core.agent import Agent
from core.agent_harness.providers.default_prompt_context import DefaultPromptContextProvider
from core.agent_harness.providers.default_providers import (
    DefaultErrorReporter,
    DefaultReasoningClientProvider,
    DefaultRunRecordFactory,
    DefaultToolProvider,
    DefaultTurnAccounting,
)
from core.agent_harness.session import Session
from gateway.polling.handle_polled_inbound_telegram_msg import GatewayAgentCallback


def build_gateway_turn_handler(
    *,
    console: Console,
) -> GatewayAgentCallback:
    """Return a callback that services one inbound gateway message.

    Action tools are resolved from the live per-chat ``session`` on every turn
    (same as the interactive shell), so integration-scoped tools stay available
    after ``SessionResolver`` hydrates the chat session. The callback drives the
    shared headless dispatch — there is no persistent per-transport agent.
    """

    def handle(
        text: str,
        session: Session,
        sink: GatewayOutputSink,
        logger: logging.Logger,
    ) -> None:
        error_reporter = DefaultErrorReporter(logger)
        turn_result = Agent.dispatch_message_to_headless_agent(
            text,
            session=session,
            output=sink,
            tools=DefaultToolProvider(
                session,
                console,
                tool_action_logger=logger,
            ),
            prompts=DefaultPromptContextProvider(session),
            reasoning=DefaultReasoningClientProvider(
                output=sink,
                error_reporter=error_reporter,
                session=session,
            ),
            run_factory=DefaultRunRecordFactory(session),
            accounting=DefaultTurnAccounting(session, text),
            error_reporter=error_reporter,
            gather_enabled=True,
        )
        outbound_text = (
            turn_result.assistant_response_text or turn_result.action_result.response_text
        ).strip()
        if not turn_result.answered and outbound_text:
            sink.finalize(outbound_text)

    return handle


__all__ = ["build_gateway_turn_handler"]

"""Gateway process entrypoint."""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

from dotenv import load_dotenv
from rich.console import Console

from config.gateway_output_sink import GatewayOutputSink
from core.agent import Agent
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.prompts.action_agent_system_prompt import _SYSTEM_PROMPT_BASE
from core.agent_harness.providers.default_prompt_context import DefaultPromptContextProvider
from core.agent_harness.providers.default_providers import (
    DefaultErrorReporter,
    DefaultReasoningClientProvider,
    DefaultRunRecordFactory,
    DefaultToolProvider,
    DefaultTurnAccounting,
)
from core.agent_harness.session import ReplSession
from core.tool_framework.registered_tool import RegisteredTool
from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import (
    GatewayConfigurationError,
    GatewaySettings,
    load_gateway_settings,
)
from gateway.polling.telegram_gateway_background import (
    TelegramGatewayBackground,
    start_telegram_gateway_background,
)
from gateway.polling.telegram_polling_runtime import (
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)


def build_gateway_agent(
    resolved_integrations: dict[str, Any],
    tools: list[RegisteredTool],
) -> Agent[RegisteredTool]:
    """Build the Agent that services one gateway turn.

    Uses the shared :func:`~core.agent_harness.agent_builder.build_agent`
    factory so the gateway shares its :class:`Agent` construction path with
    the action and evidence surfaces.
    """
    # @todo: pre-pend or modify this system prompt with gateway-specific guidance.
    config = AgentConfig(
        llm=None,
        system=_SYSTEM_PROMPT_BASE,
        tools=list(tools),
        resolved_integrations=resolved_integrations,
        max_iterations=6,
    )
    return build_agent(config)


class GatewayManager:
    """Running Telegram gateway process handle."""

    def __init__(self) -> None:
        self.settings: GatewaySettings | None = None
        self.logger: logging.Logger | None = None
        self.handle: TelegramGatewayBackground | None = None
        self.agent: Agent[RegisteredTool] | None = None

    def start_gateway(self, *, wait: bool = True) -> GatewayManager:
        """Start the Telegram gateway in long-poll mode."""
        load_dotenv(override=False)
        logger = configure_gateway_logging()

        # Getting the configured integrations
        repl_session = ReplSession()
        repl_session.hydrate_configured_integrations()
        console = Console(force_terminal=False)

        # Getting the integrations, tools and building the gateway agent
        integrations = repl_session.get_integrations().resolved_integrations
        tools = DefaultToolProvider(repl_session, console).action_tools(
            confirm_fn=None,
            is_tty=False,
        )
        gateway_agent = build_gateway_agent(integrations, tools)

        try:
            settings = load_gateway_settings()
        except GatewayConfigurationError as exc:
            print(
                f"[telegram-gateway] could not start long-poll mode: {exc}",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

        def handle_callback_to_gateway_agent(
            text: str,
            session: ReplSession,
            sink: GatewayOutputSink,
            logger: logging.Logger,
        ) -> None:
            _ = logger
            error_reporter = DefaultErrorReporter(logger)
            # This must dispatch through the gateway agent created by this manager.
            turn_result = gateway_agent.dispatch_message_to_headless_agent(
                text,
                session=session,
                output=sink,
                tools=DefaultToolProvider(
                    session,
                    console,
                    precomputed_action_tools=tools,
                    tool_action_logger=logger,
                ),
                prompts=DefaultPromptContextProvider(session),
                reasoning=DefaultReasoningClientProvider(
                    output=sink,
                    error_reporter=error_reporter,
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

        telegram_background_worker = start_telegram_gateway_background(
            settings=settings,
            logger=logger,
            initialize_runtime=initialize_telegram_polling_runtime,
            shutdown_runtime=shutdown_telegram_polling_runtime,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
        )

        def _stop(*_args: object) -> None:
            telegram_background_worker.stop()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        # Setting the agent to the gateway instance
        self.agent = gateway_agent
        self.settings = settings
        self.logger = logger
        self.telegram_background_worker = telegram_background_worker

        if wait:
            self.wait()
        return self

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Request shutdown and return whether the background worker stopped."""
        if self.telegram_background_worker is None:
            return True
        return self.telegram_background_worker.stop(timeout=timeout)

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait for the gateway worker and return whether it has stopped."""
        if self.telegram_background_worker is None:
            return True
        return self.telegram_background_worker.wait(timeout=timeout)


def start_gateway(*, wait: bool = True) -> GatewayManager:
    """Compatibility wrapper for existing CLI/import callers."""
    return GatewayManager().start_gateway(wait=wait)


def main() -> None:
    GatewayManager().start_gateway()


if __name__ == "__main__":
    main()

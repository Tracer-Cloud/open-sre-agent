"""Gateway process entrypoint."""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

from dotenv import load_dotenv
from gateway.agent.gateway_output_sink import GatewayOutputSink
from rich.console import Console

from core.agent import Agent
from core.agent_harness.action_tools import get_action_tools_from_integrations_context
from core.agent_harness.prompts.action_agent_system_prompt import _SYSTEM_PROMPT_BASE
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
from tools.interactive_shell.contracts import ToolContext


# Initializing the gateway agent
def build_gateway_agent(
    resolved_integrations: dict[str, Any],
    tools: list[RegisteredTool],
) -> Agent[RegisteredTool]:
    agent = Agent[RegisteredTool](
        system=_SYSTEM_PROMPT_BASE,  # @todo for the future: we will need to pre-pend or modify this system prompt with the gateway specific prompt
        tools=tools,
        resolved_integrations=resolved_integrations,
        max_iterations=6,
    )
    return agent


class Gateway:
    """Running Telegram gateway process handle."""

    def __init__(self) -> None:
        self.settings: GatewaySettings | None = None
        self.logger: logging.Logger | None = None
        self.handle: TelegramGatewayBackground | None = None
        self.agent: Agent[RegisteredTool] | None = None

    def start_gateway(self, *, wait: bool = True) -> Gateway:
        """Start the Telegram gateway in long-poll mode."""
        load_dotenv(override=False)
        logger = configure_gateway_logging()

   

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        # Getting the configured integrations
        repl_session = ReplSession()
        repl_session.hydrate_configured_integrations()


        # Getting the integrations, tools and building the gateway agent
        integrations = repl_session.get_integrations().resolved_integrations
        tool_context = ToolContext(
            session=repl_session,
            console=Console(force_terminal=False),
            action_already_listed=True,
        )
        tools = get_action_tools_from_integrations_context(
            tool_context,
            resolved_integrations=integrations,
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


        def handle_callback_to_gateway_agent(text: str, session: ReplSession, chat_id: str, sink: GatewayOutputSink, logger: logging.Logger) -> None:
            gateway_agent.handle_message(text, session, chat_id, sink, logger)

        handle = start_telegram_gateway_background(
            settings=settings,
            logger=logger,
            initialize_runtime=initialize_telegram_polling_runtime,
            shutdown_runtime=shutdown_telegram_polling_runtime,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
        )

        def _stop(*_args: object) -> None:
            handle.stop()

        # Setting the agent to the gateway instance
        self.agent = gateway_agent
        self.settings = settings
        self.logger = logger
        self.handle = handle

        if wait:
            self.wait()
        return self

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Request shutdown and return whether the background worker stopped."""
        if self.handle is None:
            return True
        return self.handle.stop(timeout=timeout)

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait for the gateway worker and return whether it has stopped."""
        if self.handle is None:
            return True
        return self.handle.wait(timeout=timeout)


def start_gateway(*, wait: bool = True) -> Gateway:
    """Compatibility wrapper for existing CLI/import callers."""
    return Gateway().start_gateway(wait=wait)


def main() -> None:
    Gateway().start_gateway()


if __name__ == "__main__":
    main()

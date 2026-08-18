"""Build the interactive shell's agent on the default port family.

The shell is a **channel**: it supplies :class:`ChannelAgentPorts` (tools,
prompts, gather, error reporter) and a capability policy that does **not**
apply gateway-chat withholds. Construction still goes through
:class:`DefaultPorts` — the same family the gateway pool uses — so the shell
keeps investigation / llm_provider / task_cancel and REPL slash / TTY paint.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness import OutputSink
from core.agent_harness.runtime import DefaultPorts, DefaultToolProvider, HeadlessAgent
from gateway.core.host.session_agents import ChannelAgentPorts
from surfaces.interactive_shell.grounding.cli_reference import shell_prompt_context_provider
from surfaces.interactive_shell.runtime.agent_harness_adapters import (
    ShellErrorReporter,
    resolve_output_sink,
)
from surfaces.interactive_shell.runtime.background import runner as background_runner
from surfaces.interactive_shell.runtime.integration_tool_gathering import shell_gather_ports
from surfaces.interactive_shell.runtime.investigation_adapter import (
    repl_investigation_launch_ports,
)
from surfaces.interactive_shell.runtime.llm_provider_adapter import repl_llm_provider_ports
from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports
from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import (
    ReplSubprocessPresenter,
)
from surfaces.interactive_shell.runtime.task_cancel_adapter import repl_task_cancel_ports
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver
from tools.interactive_shell.shared.investigation_launch import InvestigationLaunchPorts


def _subprocess_presenter_factory(
    session: Session,
    console: Console,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    action_already_listed: bool,
) -> ReplSubprocessPresenter:
    return ReplSubprocessPresenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


def _investigation_ports_factory() -> InvestigationLaunchPorts:
    return repl_investigation_launch_ports(
        start_background_text=background_runner.start_background_text_investigation,
        start_background_sample=background_runner.start_background_template_investigation,
    )


def _observer_factory(session: Session, console: Console) -> Callable[[str], Any]:
    def observer_factory(message: str) -> Any:
        return ActionRenderObserver(session=session, console=console, message=message)

    return observer_factory


def preserve_host_capabilities(_session: object) -> None:
    """Do not apply gateway-chat withholds (investigation / llm_provider / task_cancel).

    Chat transports use the pool default ``ensure_gateway_capability_policy``.
    The shell is a different channel and must keep those tools.
    """
    return None


def shell_channel_ports(
    *,
    request_exit: Callable[[], None] | None = None,
) -> ChannelAgentPorts:
    """The interactive shell's agent ports: REPL tools, CLI grounding, console gather."""

    def build_tools(
        session: Session,
        console: Console,
        logger: Any,
        observer: Any,
    ) -> DefaultToolProvider:
        _ = (logger, observer)
        return shell_tool_provider(session, console, request_exit=request_exit)

    return ChannelAgentPorts(
        surface="interactive_shell",
        build_tools=build_tools,
        build_prompts=shell_prompt_context_provider,
        build_gather=shell_gather_ports,
        error_reporter=ShellErrorReporter(),
        apply_capability_policy=preserve_host_capabilities,
    )


def shell_tool_provider(
    session: Session,
    console: Console,
    *,
    request_exit: Callable[[], None] | None = None,
) -> DefaultToolProvider:
    """The shell's tools: the harness provider with the shell's port factories."""
    return DefaultToolProvider(
        session,
        console,
        request_exit=request_exit,
        observer_factory=_observer_factory(session, console),
        subprocess_presenter_factory=_subprocess_presenter_factory,
        investigation_ports_factory=_investigation_ports_factory,
        llm_provider_ports_factory=repl_llm_provider_ports,
        task_cancel_ports_factory=repl_task_cancel_ports,
        slash_ports_factory=repl_slash_ports,
    )


def build_shell_agent(
    session: Session,
    console: Console,
    *,
    output: OutputSink | None = None,
    request_exit: Callable[[], None] | None = None,
) -> HeadlessAgent:
    """One shell agent from :func:`shell_channel_ports`; per-turn values via ``bind_turn``."""
    ports = shell_channel_ports(request_exit=request_exit)
    policy = ports.apply_capability_policy or preserve_host_capabilities
    policy(session)
    build_tools = ports.build_tools or (
        lambda sess, cons, _log, _obs: shell_tool_provider(sess, cons, request_exit=request_exit)
    )
    logger = logging.getLogger("opensre.interactive_shell")
    return DefaultPorts(
        session=session,
        output=resolve_output_sink(console, output),
        console=console,
        surface=ports.surface,
        error_reporter=ports.error_reporter,
    ).agent(
        tools=build_tools(session, console, logger, None),
        prompts=ports.build_prompts(session) if ports.build_prompts else None,
        gather=ports.build_gather(session, console) if ports.build_gather else None,
    )


__all__ = [
    "build_shell_agent",
    "preserve_host_capabilities",
    "shell_channel_ports",
    "shell_tool_provider",
]

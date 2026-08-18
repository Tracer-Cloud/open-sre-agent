"""Build the interactive shell's agent on the default port family.

The shell's ports: its prompt provider (CLI catalog + repo grounding), its
console-bound output sink, its Sentry-forwarding error reporter, its gather
ports (console progress lines, tool calls persisted to the session), and the
tool-provider port factories (subprocess presenter, investigation launch, LLM
provider, task cancel, slash commands). Answering, gathering and action
execution are the agent's own; the shell adds no stage of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness import OutputSink
from core.agent_harness.runtime import DefaultPorts, DefaultToolProvider, HeadlessAgent
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
    """One shell agent on the default port family; per-turn values come via ``bind_turn``."""
    return DefaultPorts(
        session=session,
        output=resolve_output_sink(console, output),
        console=console,
        error_reporter=ShellErrorReporter(),
    ).agent(
        tools=shell_tool_provider(session, console, request_exit=request_exit),
        prompts=shell_prompt_context_provider(session),
        gather=shell_gather_ports(session, console),
    )


__all__ = ["build_shell_agent", "shell_tool_provider"]

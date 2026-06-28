"""Interactive-shell adapters implementing the :mod:`core.agent_harness.ports` Protocols.

These wire the decoupled :mod:`agent` turn engine to the terminal surface: the
Rich console, the ``ReplSession``, the tool registry, the grounding caches, and
the shell's telemetry. The engine itself never imports any of these — it talks
only to the ports, and the shell supplies these concrete implementations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from rich.console import Console
from rich.markup import escape

from core.agent_harness.grounding.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)
from core.agent_harness.prompts import build_environment_block
from core.agent_harness.session import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST
from interactive_shell.runtime import ReplSession
from interactive_shell.runtime.core.token_accounting import build_llm_run_info
from interactive_shell.ui import (
    ERROR,
    stream_to_console,
)
from interactive_shell.ui.action_rendering import ActionRenderObserver
from interactive_shell.ui.streaming import render_response_header
from interactive_shell.utils.error_handling.exception_reporting import report_exception
from tools.interactive_shell.contracts import ToolContext
from tools.interactive_shell.registry import REGISTRY


class ShellOutputSink:
    """:class:`core.agent_harness.ports.OutputSink` over a Rich console."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def print(self, message: str = "") -> None:
        self._console.print(message)

    def render_response_header(self, label: str) -> None:
        render_response_header(self._console, label)

    def render_error(self, message: str) -> None:
        self._console.print(f"[yellow]{escape(message)}[/]")

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
    ) -> str:
        return stream_to_console(
            self._console,
            label=label,
            chunks=iter(chunks),
            suppress_if_starts_with=suppress_if_starts_with,
        )


class ShellToolProvider:
    """:class:`core.agent_harness.ports.ToolProvider` backed by the shell tool registry."""

    def __init__(self, session: ReplSession, console: Console) -> None:
        self._session = session
        self._console = console

    def action_tools(
        self, *, confirm_fn: Callable[[str], str] | None, is_tty: bool | None
    ) -> list[Any]:
        ctx = ToolContext(
            session=self._session,
            console=self._console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            action_already_listed=True,
        )
        return REGISTRY.agent_tools_for_context(ctx)

    def observer(self, *, message: str) -> Callable[[str, dict[str, Any]], None]:
        return ActionRenderObserver(session=self._session, console=self._console, message=message)


class ShellPromptContextProvider:
    """:class:`core.agent_harness.ports.PromptContextProvider` over the session grounding caches."""

    def __init__(self, session: ReplSession) -> None:
        self._session = session

    def cli_reference(self) -> str:
        return self._session.grounding.cli.build_text()

    def agents_md(self) -> str:
        return self._session.grounding.agents_md.build_text()

    def investigation_flow(self) -> str:
        return build_investigation_flow_reference_text()

    def environment_block(self) -> str:
        return build_environment_block(
            integrations=tuple(self._session.configured_integrations),
            known=self._session.configured_integrations_known,
        )

    def suggested_synthetic_prompt(self) -> str:
        return SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

    def log_diagnostics(self, reason: str) -> None:
        self._session.grounding.log_cache_diagnostics(reason)


class ShellReasoningClientProvider:
    """:class:`core.agent_harness.ports.ReasoningClientProvider` for the streaming assistant."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def get(self) -> Any | None:
        try:
            from core.llm.llm_client import get_llm_for_reasoning
        except Exception as exc:
            report_exception(exc, context="interactive_shell.cli_agent.import")
            self._console.print(f"[{ERROR}]LLM client unavailable:[/] {escape(str(exc))}")
            return None
        return get_llm_for_reasoning()


class ShellRunRecordFactory:
    """:class:`core.agent_harness.ports.RunRecordFactory` producing the shell ``LlmRunInfo``."""

    def __init__(self, session: ReplSession) -> None:
        self._session = session

    def build(self, *, client: Any, prompt: str, response_text: str, started: float) -> Any:
        return build_llm_run_info(
            session=self._session,
            prompt=prompt,
            response_text=response_text,
            started=started,
            client=client,
        )


class ShellErrorReporter:
    """:class:`core.agent_harness.ports.ErrorReporter` over ``report_exception``."""

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        report_exception(exc, context=context, expected=expected)


__all__ = [
    "ShellErrorReporter",
    "ShellOutputSink",
    "ShellPromptContextProvider",
    "ShellReasoningClientProvider",
    "ShellRunRecordFactory",
    "ShellToolProvider",
]

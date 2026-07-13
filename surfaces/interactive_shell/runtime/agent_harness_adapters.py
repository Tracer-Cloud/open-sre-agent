"""Interactive-shell output adapter implementing :mod:`core.agent_harness.ports`.

This module owns terminal rendering only. Shared action-tool, reasoning-client,
run-record, and error-reporting providers live in :mod:`core.agent_harness`.
"""

from __future__ import annotations

from collections.abc import Iterable

from rich.console import Console
from rich.markup import escape

from core.agent_harness.ports import OutputSink
from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from surfaces.interactive_shell.ui import (
    stream_to_console,
)
from surfaces.interactive_shell.ui.streaming import render_response_header


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
        # On a credit/billing wall, add the in-tool recovery hint.
        if CREDIT_EXHAUSTED_MARKER in message:
            self._console.print("[dim]Run /model to switch to another provider.[/]")
            self._console.print(
                "[dim]Or run /auth login <provider> to re-authenticate "
                "or add a different provider.[/]"
            )

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


def resolve_output_sink(console: Console, output: OutputSink | None) -> OutputSink:
    """Return the caller's sink, or a shell sink bound to ``console``."""
    if output is not None:
        return output
    return ShellOutputSink(console)


__all__ = ["ShellOutputSink", "resolve_output_sink"]

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
from surfaces.interactive_shell.ui.streaming import (
    StreamPaintResult,
    finish_deferred_closer,
    publish_full_response,
    render_response_header,
    stream_to_console,
    stream_to_console_state,
)


class ShellOutputSink:
    """:class:`core.agent_harness.ports.OutputSink` over a Rich console.

    The console may be rebound per turn (spinner-aware streaming console) so a
    long-lived :class:`~core.agent_harness.turns.action_driver.ActionTurnRunner`
    can keep the same sink object.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._paint: StreamPaintResult | None = None
        self._defer_want_me_to_closer = False

    def bind_console(self, console: Console) -> None:
        """Point subsequent output at ``console`` for the current turn."""
        self._console = console

    def print(self, message: str = "") -> None:
        """Render harness text literally.

        Everything the harness prints here is data — tool output, model
        replies, skill bodies. A ``sed 's/\\]\\]>//'`` in a shell command
        reads to Rich as an unbalanced markup tag and raised ``MarkupError``,
        which took the whole turn down. Styled output goes through the
        ``render_*`` methods instead.
        """
        self._console.print(message, markup=False)

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
        defer_want_me_to_closer: bool = False,
    ) -> str:
        self._defer_want_me_to_closer = defer_want_me_to_closer
        if defer_want_me_to_closer:
            paint = stream_to_console_state(
                self._console,
                label=label,
                chunks=iter(chunks),
                suppress_if_starts_with=suppress_if_starts_with,
                defer_want_me_to_closer=True,
            )
            self._paint = paint
            return paint.text
        self._paint = None
        return stream_to_console(
            self._console,
            label=label,
            chunks=iter(chunks),
            suppress_if_starts_with=suppress_if_starts_with,
        )

    def finish_streamed_response(self, text: str) -> None:
        """Flush a deferred / rewritten Want-me-to closer after gather normalize."""
        paint = self._paint
        defer = self._defer_want_me_to_closer
        self._paint = None
        self._defer_want_me_to_closer = False
        if not defer or paint is None:
            return
        if not paint.deferred_closer and text == paint.text:
            return
        # Non-TTY deferred holds the entire answer until normalize.
        if not self._console.is_terminal and paint.deferred_closer:
            publish_full_response(self._console, text)
            return
        finish_deferred_closer(
            self._console,
            text,
            footer_elapsed_s=paint.footer_elapsed_s,
            footer_total_bytes=paint.footer_total_bytes,
        )


def resolve_output_sink(console: Console, output: OutputSink | None) -> OutputSink:
    """Return the caller's sink, or a shell sink bound to ``console``."""
    if output is not None:
        return output
    return ShellOutputSink(console)


__all__ = ["ShellOutputSink", "resolve_output_sink"]

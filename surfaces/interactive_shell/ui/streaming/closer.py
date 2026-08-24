"""Flushing a deferred Want-me-to closer after gather normalizes the final answer."""

from __future__ import annotations

from rich.console import Console

import infrastructure.terminal.theme as ui_theme
from core.agent_harness.spi.prompt_chrome import closer_tail_from
from surfaces.interactive_shell.ui.streaming.loop import _CHARS_PER_TOKEN, _format_tokens
from surfaces.interactive_shell.ui.streaming.renderer import render_markdown_block


def finish_deferred_closer(
    console: Console,
    final_text: str,
    *,
    footer_elapsed_s: float | None = None,
    footer_total_bytes: int | None = None,
) -> None:
    """Render the (possibly rewritten) Want-me-to closer + held stream footer."""
    closer = closer_tail_from(final_text)
    if closer:
        render_markdown_block(console, closer)
    if footer_elapsed_s is not None and footer_total_bytes is not None:
        tokens = _format_tokens(footer_total_bytes // _CHARS_PER_TOKEN)
        console.print(f"[{ui_theme.DIM}]· {footer_elapsed_s:.1f}s · ↓ {tokens}[/]")
    console.print()

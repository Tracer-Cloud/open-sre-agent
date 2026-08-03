"""StreamingConsole honors an injected output console (embed capture path)."""

from __future__ import annotations

import io
import threading
from typing import Any

from rich.console import Console
from rich.theme import Theme

from surfaces.interactive_shell.ui.streaming.console import StreamingConsole


class _Spinner:
    bytes_in = 0
    streaming = False

    def stop(self) -> None:
        return None


def test_streaming_console_with_output_skips_tty_prep(monkeypatch: Any) -> None:
    """When ``output=`` is set, prints go through the caller — no TTY prep."""
    prep_calls: list[str] = []

    def _boom_prepare() -> None:
        prep_calls.append("prepare")

    def _boom_ensure() -> None:
        prep_calls.append("ensure")

    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.choice_menu.prepare_repl_output_line",
        _boom_prepare,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.choice_menu.ensure_tty_column_zero",
        _boom_ensure,
    )

    buf = io.StringIO()
    target = Console(file=buf, force_terminal=False, highlight=False)
    console = StreamingConsole(
        _Spinner(),
        threading.Event(),
        output=target,
        force_terminal=False,
        highlight=False,
    )
    console.print("hello-embed")

    assert "hello-embed" in buf.getvalue()
    assert prep_calls == []


def test_streaming_console_without_output_still_prepares_tty(monkeypatch: Any) -> None:
    """Interactive path keeps one TTY prepare call before printing."""
    prep_calls: list[str] = []

    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.choice_menu.prepare_repl_output_line",
        lambda: prep_calls.append("prepare"),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.choice_menu.ensure_tty_column_zero",
        lambda: prep_calls.append("ensure"),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.rendering._repl_output_already_prepared",
        lambda: False,
    )
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    buf = io.StringIO()
    console = StreamingConsole(
        _Spinner(),
        threading.Event(),
        file=buf,
        force_terminal=False,
        highlight=False,
    )
    console.print("tty-path")

    assert "tty-path" in buf.getvalue()
    assert "prepare" in prep_calls


def test_streaming_console_use_theme_forwards_to_output() -> None:
    """Theme pushes must land on the embedder console, not the adapter."""
    buf = io.StringIO()
    target = Console(file=buf, force_terminal=False, highlight=False)
    console = StreamingConsole(
        _Spinner(),
        threading.Event(),
        output=target,
        force_terminal=False,
        highlight=False,
    )
    theme = Theme({"info": "cyan"})
    ctx = console.use_theme(theme)
    assert ctx.console is target
    with ctx:
        pass

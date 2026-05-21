"""Tests for Rich rendering helpers used by the interactive shell."""

from __future__ import annotations

import io
import threading

import pytest
from rich.console import Console

from app.cli.interactive_shell.runtime import loop
from app.cli.interactive_shell.ui.rendering import (
    print_planned_actions,
    render_integrations_table,
    render_mcp_table,
    repl_print,
    repl_table,
)


def test_repl_table_minimal_box() -> None:
    t = repl_table(title="T")
    assert t.title == "T"


def test_render_integrations_table_empty_shows_hint() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    render_integrations_table(console, [])
    assert "opensre onboard" in buf.getvalue()


def test_repl_print_resets_before_each_line(monkeypatch) -> None:
    resets: list[bool] = []

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    repl_print(console, "line one")
    repl_print(console, "line two")

    assert len(resets) == 2


def test_repl_print_does_not_double_prepare_with_streaming_console(monkeypatch) -> None:
    resets: list[bool] = []

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    console = loop.StreamingConsole(
        loop.SpinnerState(),
        threading.Event(),
        file=io.StringIO(),
        force_terminal=False,
        width=80,
    )
    repl_print(console, "line")

    assert len(resets) == 1


def test_repl_print_streaming_console_prepares_tty_once_when_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.repl_tty_interactive",
        lambda: True,
    )

    console = loop.StreamingConsole(
        loop.SpinnerState(),
        threading.Event(),
        file=io.StringIO(),
        force_terminal=False,
        width=80,
    )
    repl_print(console, "line")

    assert fake_stdout.writes == ["\r\n", "\r"]


def test_render_integrations_table_resets_tty_before_print(monkeypatch) -> None:
    """Regression: padded inline menus leave the cursor at a high column."""
    resets: list[bool] = []

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    render_integrations_table(
        console,
        [
            {
                "service": "grafana",
                "source": "local store",
                "status": "passed",
                "detail": "Connected to https://example.grafana.net",
            }
        ],
    )

    assert len(resets) == 1
    assert "grafana" in buf.getvalue()


def test_render_mcp_table_prepares_tty_once(monkeypatch) -> None:
    resets: list[bool] = []

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    render_mcp_table(
        console,
        [
            {
                "service": "github",
                "source": "local store",
                "status": "configured",
                "detail": "Connected",
            }
        ],
    )

    assert len(resets) == 1
    assert "github" in buf.getvalue()


def test_print_planned_actions_formats_kinds() -> None:
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
        PlannedAction,
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    print_planned_actions(
        console,
        [
            PlannedAction(kind="slash", content="/health", position=0),
            PlannedAction(kind="shell", content="pwd", position=10),
        ],
    )
    out = buf.getvalue()
    assert "/health" in out
    assert "pwd" in out

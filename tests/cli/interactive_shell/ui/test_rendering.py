"""Tests for Rich rendering helpers used by the interactive shell."""

from __future__ import annotations

import io
import threading

import pytest
from rich.console import Console

from app.cli.interactive_shell.config.tool_catalog import ToolCatalogEntry
from app.cli.interactive_shell.runtime import loop
from app.cli.interactive_shell.ui.rendering import (
    print_planned_actions,
    render_integrations_table,
    render_mcp_table,
    render_tools_table,
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

    assert fake_stdout.writes == ["\n\x1b[0G", "line\n"]


def test_render_integrations_table_resets_tty_before_print(monkeypatch) -> None:
    """Regression: padded inline menus leave the cursor at a high column."""
    terminal = io.StringIO()
    proxy = io.StringIO()

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.repl_tty_interactive",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.repl_terminal_stdout",
        lambda: terminal,
    )

    console = Console(file=proxy, force_terminal=False, width=80)
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

    assert terminal.getvalue().startswith("\n\x1b[0G")
    assert "grafana" in terminal.getvalue()
    assert proxy.getvalue() == ""


def test_render_mcp_table_prepares_tty_once(monkeypatch) -> None:
    terminal = io.StringIO()
    proxy = io.StringIO()

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.repl_tty_interactive",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.choice_menu.repl_terminal_stdout",
        lambda: terminal,
    )

    console = Console(file=proxy, force_terminal=False, width=80)
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

    assert terminal.getvalue().startswith("\n\x1b[0G")
    assert "github" in terminal.getvalue()
    assert proxy.getvalue() == ""


def test_render_tools_table_shows_tool_rows() -> None:
    catalog = [
        ToolCatalogEntry(
            name="MyTool",
            surfaces=("investigation", "chat"),
            description="Does something useful",
            source_file="app/tools/my_tool.py",
            input_schema_summary="query: string",
        ),
        ToolCatalogEntry(
            name="AnotherTool",
            surfaces=("investigation",),
            description="Fetches metrics",
            source_file="app/tools/another.py",
            input_schema_summary="(no params)",
        ),
    ]
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    render_tools_table(console, catalog)
    out = buf.getvalue()
    assert "Tools" in out
    assert "MyTool" in out
    assert "AnotherTool" in out
    assert "chat, investigation" in out
    assert "Does something useful" in out


def test_render_tools_table_empty_shows_hint() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    render_tools_table(console, [])
    assert "no tools registered" in buf.getvalue()


def test_render_models_table_has_header_row() -> None:
    """render_models_table must show column headers for visual parity with other tables."""

    class _FakeLLM:
        provider = "anthropic"
        anthropic_reasoning_model = "claude-opus-4"
        anthropic_toolcall_model = "claude-haiku-4"

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    from app.cli.interactive_shell.ui.rendering import render_models_table

    render_models_table(console, _FakeLLM())
    out = buf.getvalue()
    # Title and row values
    assert "LLM connection" in out
    assert "anthropic" in out
    # "setting" column header must appear now that show_header is True
    assert "setting" in out


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

"""Tests for Rich rendering helpers used by the interactive shell."""

from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell.rendering import (
    print_planned_actions,
    render_integrations_table,
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


def test_print_planned_actions_formats_kinds() -> None:
    from app.cli.interactive_shell.interaction_models import PlannedAction

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

"""Tests for interactive-shell action rendering."""

from __future__ import annotations

import io

from rich.console import Console

from core.agent_harness.session import ReplSession
from interactive_shell.ui.action_rendering import ActionRenderObserver


def test_action_observer_records_without_printing_internal_plan() -> None:
    session = ReplSession()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False)
    observer = ActionRenderObserver(session=session, console=console, message="run /model show")

    observer(
        "tool_start",
        {"name": "slash_invoke", "input": {"command": "/model", "args": ["show"]}},
    )

    assert session.history == [{"type": "cli_agent", "text": "run /model show", "ok": True}]
    assert observer.planned_count == 1
    assert buffer.getvalue() == ""

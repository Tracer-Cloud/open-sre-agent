"""Tests for the agent ask_user_choice tool.

Focus: the tool must never run a raw-stdin picker inline. On an interactive
REPL it stores the pending choice and defers to the ``/choose`` exclusive-stdin
turn; on non-TTY / headless surfaces it tells the model to fall back to a
numbered list.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from core.agent_harness.tools.tool_context import ActionToolContext
from core.agent_harness.turns.headless_adapters import InMemorySessionStore
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.ask_choice import (
    ask_user_choice_tool,
    execute_ask_user_choice_tool,
)

_TITLE = "How should I handle the uncommitted changes?"
_OPTIONS = [
    "Stash the changes (recommended – quick & safe)",
    "Commit the changes",
    "Use a separate git worktree",
]


@dataclass
class _Ports:
    """Minimal slash-ports fake: the tool only consults ``tty_interactive``."""

    tty: bool = True

    def tty_interactive(self) -> bool:
        return self.tty


def _ctx(
    *,
    session: Any | None = None,
    ports: _Ports | None = None,
    is_tty: bool | None = None,
) -> ActionToolContext:
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    return ActionToolContext(
        session=session if session is not None else Session(),
        console=console,
        slash_ports=ports if ports is not None else _Ports(),
        is_tty=is_tty,
    )


def test_ask_user_choice_tool_is_action_surface_read_only() -> None:
    assert ask_user_choice_tool.name == "ask_user_choice"
    assert "action" in ask_user_choice_tool.surfaces
    assert ask_user_choice_tool.side_effect_level == "read_only"
    assert ask_user_choice_tool.parallel_safe is False


def test_interactive_repl_defers_menu_to_choose_turn() -> None:
    session = Session()
    ctx = _ctx(session=session)

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["ok"] is True
    assert result["menu"] == "queued"
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _TITLE
    assert session.pending_user_choice.options == tuple(_OPTIONS)
    assert session.terminal.pending_prompt_default == "/choose"
    assert session.terminal.pending_prompt_autosubmit is True


def test_non_tty_ports_fall_back_to_numbered_list() -> None:
    session = Session()
    ctx = _ctx(session=session, ports=_Ports(tty=False))

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["ok"] is True
    assert result["menu"] == "unavailable"
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None


def test_explicit_non_tty_turn_falls_back() -> None:
    session = Session()
    ctx = _ctx(session=session, is_tty=False)

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["menu"] == "unavailable"
    assert session.pending_user_choice is None


def test_headless_session_without_terminal_falls_back() -> None:
    session = InMemorySessionStore()
    ctx = _ctx(session=session)

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["ok"] is True
    assert result["menu"] == "unavailable"


def test_missing_title_is_rejected() -> None:
    result = execute_ask_user_choice_tool({"title": "  ", "options": _OPTIONS}, _ctx())
    assert result["ok"] is False


def test_fewer_than_two_distinct_options_is_rejected() -> None:
    session = Session()
    ctx = _ctx(session=session)

    result = execute_ask_user_choice_tool(
        {"title": _TITLE, "options": ["Stash", "Stash", "  "]},
        ctx,
    )

    assert result["ok"] is False
    assert session.pending_user_choice is None


def test_too_many_options_is_rejected() -> None:
    options = [f"Option {index}" for index in range(9)]
    result = execute_ask_user_choice_tool({"title": _TITLE, "options": options}, _ctx())
    assert result["ok"] is False

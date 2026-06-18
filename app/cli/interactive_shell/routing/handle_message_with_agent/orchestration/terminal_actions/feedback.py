"""User-visible feedback and history helpers for terminal action execution."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui.streaming import render_response_header

_CLI_AGENT_MSG_CAP = 24  # mirrors _MAX_CLI_AGENT_TURNS * 2 in cli_agent.py


def render_plan_denied(console: Console) -> None:
    console.print()
    render_response_header(console, "assistant")
    console.print(
        "[yellow]I couldn't safely decide actions for that request.[/] "
        "Please rephrase or use explicit slash commands."
    )


def render_planner_llm_error(console: Console, message: str) -> None:
    console.print()
    render_response_header(console, "assistant")
    console.print(f"[yellow]{escape(message)}[/]")


def persist_error_turn(session: ReplSession, user_text: str, error_text: str) -> None:
    """Record a failed assistant turn in cli_agent_messages so /resume can display it."""
    session.cli_agent_messages.append(("user", user_text))
    session.cli_agent_messages.append(("assistant", error_text))
    if len(session.cli_agent_messages) > _CLI_AGENT_MSG_CAP:
        session.cli_agent_messages[:] = session.cli_agent_messages[-_CLI_AGENT_MSG_CAP:]


__all__ = [
    "persist_error_turn",
    "render_plan_denied",
    "render_planner_llm_error",
]

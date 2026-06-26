"""Session lifecycle slash commands: /clear, /new, /sessions, /resume."""

from __future__ import annotations

from cli.interactive_shell.command_registry.session_cmds.lifecycle import _cmd_clear, _cmd_new
from cli.interactive_shell.command_registry.session_cmds.list import _cmd_sessions
from cli.interactive_shell.command_registry.session_cmds.resume import (
    _apply_resume_data,
    _cmd_resume,
)
from cli.interactive_shell.command_registry.types import SlashCommand

COMMANDS: list[SlashCommand] = [
    SlashCommand("/clear", "Clear the screen and re-render the banner.", _cmd_clear),
    SlashCommand(
        "/sessions",
        "List recent REPL sessions.",
        _cmd_sessions,
        usage=("/sessions",),
    ),
    SlashCommand(
        "/resume",
        "Resume a previous session by restoring its conversation context.",
        _cmd_resume,
        usage=("/resume <session-id-prefix>",),
        notes=(
            "Restores cli_agent_messages and accumulated infra context from the chosen session.",
            "Bare /resume opens an interactive session picker in a TTY.",
            "Accepts a session ID prefix or a name substring (e.g. /resume redis).",
            "Replaces the current session's LLM conversation context; warns if messages exist.",
        ),
    ),
    SlashCommand(
        "/new",
        "Start a new session while keeping the current conversation context.",
        _cmd_new,
        notes=(
            "Unlike /clear, /new rotates the session ID and resets state while keeping LLM context.",
            "Use after /resume to continue a conversation in a clean session file.",
        ),
    ),
]

__all__ = ["COMMANDS", "_apply_resume_data"]

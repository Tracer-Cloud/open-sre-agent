"""Session lifecycle slash commands: /clear, /new, /sessions, /resume, /compact, /goal."""

from __future__ import annotations

from surfaces.interactive_shell.command_registry.session_cmds.goal import _cmd_goal
from surfaces.interactive_shell.command_registry.session_cmds.lifecycle import (
    _cmd_clear,
    _cmd_compact,
    _cmd_new,
)
from surfaces.interactive_shell.command_registry.session_cmds.list import _cmd_sessions
from surfaces.interactive_shell.command_registry.session_cmds.resume import (
    _apply_resume_data,
    _cmd_resume,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand

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
        usage=("/resume <session-id-prefix>", "/resume <session-id-prefix>:<entry-id-prefix>"),
        notes=(
            "Restores cli_agent_messages and accumulated infra context from the chosen session.",
            "Bare /resume opens an interactive session picker in a TTY.",
            "Accepts a session ID prefix, entry ref, or name substring (e.g. /resume redis).",
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
    SlashCommand(
        "/compact",
        "Compact the current session context into a replayable summary entry.",
        _cmd_compact,
        usage=("/compact",),
        notes=(
            "Writes a compaction entry and keeps the most recent messages plus a summary.",
            "Useful before continuing a long-running investigation in the same REPL.",
        ),
    ),
    SlashCommand(
        "/goal",
        "Show, set, or clear the outer session goal (multi-step continuation).",
        _cmd_goal,
        usage=(
            "/goal",
            "/goal show",
            "/goal set [--max-turns N] <condition>",
            "/goal clear",
        ),
        notes=(
            "Sugar over SessionGoal / chat_until_goal — distinct from /work durable todos.",
            "The action agent can also attach goals via session_goal:… handoff tags.",
        ),
        first_arg_completions=(
            ("show", "show the active session goal and checklist"),
            ("set", "attach an outer session goal (/goal set <condition>)"),
            ("clear", "clear the active session goal"),
        ),
    ),
]

__all__ = ["COMMANDS", "_apply_resume_data"]

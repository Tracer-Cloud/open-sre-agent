"""Terminal facet accessors that work on SessionCore and interactive Session.

Gateway and other headless surfaces use :class:`~core.agent_harness.session.SessionCore`,
which has no REPL terminal facet. Slash dispatch and delegated CLI commands still
need the small slice of terminal state those paths touch (dedup sets, outcome hints,
background-mode flags). These helpers read the shell terminal when present and fall
back to lightweight per-session state on headless sessions.
"""

from __future__ import annotations

from typing import Any


def session_terminal(session: Any) -> Any | None:
    return getattr(session, "terminal", None)


def agent_turn_executed_slashes(session: Any) -> set[str]:
    terminal = session_terminal(session)
    if terminal is not None:
        executed_slashes: set[str] = terminal.agent_turn_executed_slashes
        return executed_slashes
    executed: set[str] | None = getattr(session, "_headless_agent_turn_executed_slashes", None)
    if executed is None:
        executed = set()
        session._headless_agent_turn_executed_slashes = executed
    return executed


def exclusive_stdin_active(session: Any) -> bool:
    terminal = session_terminal(session)
    if terminal is not None:
        return bool(terminal.exclusive_stdin_active)
    return False


def background_mode_enabled(session: Any) -> bool:
    terminal = session_terminal(session)
    if terminal is not None:
        return bool(terminal.background_mode_enabled)
    return False


def trust_mode_enabled(session: Any) -> bool:
    terminal = session_terminal(session)
    if terminal is not None:
        return bool(terminal.trust_mode)
    return False


def pop_turn_outcome_hint(session: Any) -> str:
    terminal = session_terminal(session)
    if terminal is not None:
        pop_hint = getattr(terminal, "pop_turn_outcome_hint", None)
        if callable(pop_hint):
            hint = pop_hint()
            return hint.strip() if isinstance(hint, str) else ""
    hints = getattr(session, "_headless_turn_outcome_hints", None)
    if isinstance(hints, list) and hints:
        return str(hints.pop()).strip()
    return ""


def set_turn_outcome_hint(session: Any, hint: str) -> None:
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.set_turn_outcome_hint(hint)
        return
    hints = getattr(session, "_headless_turn_outcome_hints", None)
    if hints is None:
        hints = []
        session._headless_turn_outcome_hints = hints
    hints.append(hint)


def set_auto_command(session: Any, command: str) -> None:
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.set_auto_command(command)
        return
    set_turn_outcome_hint(
        session,
        f"Run `{command}` in the interactive shell (`uv run opensre`).",
    )


__all__ = [
    "agent_turn_executed_slashes",
    "background_mode_enabled",
    "exclusive_stdin_active",
    "pop_turn_outcome_hint",
    "session_terminal",
    "set_auto_command",
    "set_turn_outcome_hint",
    "trust_mode_enabled",
]

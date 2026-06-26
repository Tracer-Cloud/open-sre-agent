"""REPL-scoped analytics context for joinable lifecycle events."""

from __future__ import annotations

from contextvars import ContextVar, Token

_CLI_SESSION_ID: ContextVar[str | None] = ContextVar("cli_session_id", default=None)


def get_cli_session_id() -> str | None:
    """Return the active interactive-shell session id, if any."""
    return _CLI_SESSION_ID.get()


def bind_cli_session_id(session_id: str | None) -> Token[str | None]:
    """Bind ``cli_session_id`` for the current async/task context."""
    return _CLI_SESSION_ID.set(session_id)


def reset_cli_session_id(token: Token[str | None]) -> None:
    """Restore the previous ``cli_session_id`` binding."""
    _CLI_SESSION_ID.reset(token)

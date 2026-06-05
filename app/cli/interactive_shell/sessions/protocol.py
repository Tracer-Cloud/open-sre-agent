"""Persistence-facing session shape for JSONL store writes (avoids runtime import cycle)."""

from __future__ import annotations

from typing import Any, Protocol


class SessionPersistenceSource(Protocol):
    """Fields read by :class:`~app.cli.interactive_shell.sessions.store.SessionStore`."""

    session_id: str
    started_at: float
    cli_agent_messages: list[tuple[str, str]]
    accumulated_context: dict[str, Any]

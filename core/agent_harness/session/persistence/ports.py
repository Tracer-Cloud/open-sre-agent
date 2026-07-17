"""Session persistence contracts for the interactive shell.

These protocols decouple the in-memory session object (:class:`Session`)
and the slash-command surfaces from any concrete persistence backend. Two
roles are kept deliberately separate, mirroring a storage-vs-repository split:

- :class:`SessionStorage` — per-session lifecycle and entry writes for a single
  logical session (open, append, flush, reopen). Backends: JSONL (production)
  and in-memory (tests).
- :class:`SessionRepo` — cross-session queries over every stored session
  (list recent, load one for ``/resume``, browse RCA history).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.state import MutableAgentState

# Turn kinds that represent user-initiated chat messages. Session.record()
# is called with the turn kind, not a normalized "chat" label, so this set must
# cover all kinds that produce conversational turns.
CHAT_KINDS: frozenset[str] = frozenset({"chat", "cli_agent", "follow_up"})


class SessionPersistenceSource(Protocol):
    """Fields a :class:`SessionStorage` backend reads off a live session."""

    session_id: str
    started_at: float
    agent: MutableAgentState
    accumulated_context: dict[str, Any]


@runtime_checkable
class SessionStorage(Protocol):
    """Per-session persistence backend for one logical session."""

    def open_session(self, session: SessionPersistenceSource) -> None:
        raise NotImplementedError

    def append_turn(self, session: SessionPersistenceSource, kind: str, text: str) -> None:
        raise NotImplementedError

    def append_turn_detail(
        self,
        session_id: str,
        kind: str,
        prompt: str,
        *,
        response: str | None = None,
        turn_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        latency_ms: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        raise NotImplementedError

    def append_tool_call(
        self,
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        result: str,
        ok: bool,
        source: str | None = None,
    ) -> None:
        raise NotImplementedError

    def append_tool_update(
        self,
        session_id: str,
        *,
        tool: str,
        update: Any,
        tool_call_id: str | None = None,
    ) -> str:
        raise NotImplementedError

    def append_compaction(
        self,
        session_id: str,
        *,
        summary: str,
        first_kept_entry_id: str,
        before_chars: int,
        after_chars: int,
        before_tokens: int | None = None,
        after_tokens: int | None = None,
    ) -> str:
        raise NotImplementedError

    def append_investigation_result(
        self,
        session_id: str,
        state: dict[str, Any],
        *,
        trigger: str = "",
    ) -> str:
        raise NotImplementedError

    def flush(self, session: SessionPersistenceSource) -> None:
        raise NotImplementedError

    def reopen_session(self, session_id: str) -> None:
        raise NotImplementedError


@runtime_checkable
class SessionRepo(Protocol):
    """Cross-session query/lifecycle surface over all stored sessions."""

    def load_recent(self, n: int = 20) -> list[dict[str, Any]]:
        raise NotImplementedError

    def count_prefix_matches(self, prefix: str) -> int:
        raise NotImplementedError

    def load_session(self, session_id_prefix: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def load_investigation_history(self, n: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError

    def lookup_investigation(self, prefix: str) -> tuple[dict[str, Any] | None, int]:
        raise NotImplementedError

    def load_investigation(self, prefix: str) -> dict[str, Any] | None:
        raise NotImplementedError

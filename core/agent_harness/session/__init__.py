"""Canonical home for agent session state and persistence.

This package centralizes reusable session state in one place:

- :class:`Session` — the in-memory session domain object (``state``).
- :class:`SessionStorage` / :class:`SessionRepo` protocols plus their JSONL and
  in-memory backends — persistence, split into per-session writes (storage) and
  cross-session queries (repo).

``Session`` delegates all persistence to an injected ``SessionStorage`` so
the on-disk format is swappable and tests can run without touching the
filesystem. The module-level ``DEFAULT_SESSION_STORAGE`` / ``DEFAULT_SESSION_REPO``
singletons provide the production JSONL backends used by agent surfaces.
"""

from __future__ import annotations

from core.agent_harness.session.repo import JsonlSessionRepo
from core.agent_harness.session.state import (
    SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
    Session,
)
from core.agent_harness.session.storage import (
    InMemorySessionStorage,
    JsonlSessionStorage,
)
from core.agent_harness.session.terminal_metrics import (
    InterventionKind,
    TerminalMetrics,
    TerminalMetricsSnapshot,
)
from core.agent_harness.session.types import (
    CHAT_KINDS,
    SessionPersistenceSource,
    SessionRepo,
    SessionStorage,
)

# Production singletons. Both backends are stateless, so sharing one instance
# across the process is safe and avoids re-instantiation on every session.
DEFAULT_SESSION_STORAGE: SessionStorage = JsonlSessionStorage()
DEFAULT_SESSION_REPO: SessionRepo = JsonlSessionRepo()


def default_session_storage() -> SessionStorage:
    """Return the shared production JSONL storage backend."""
    return DEFAULT_SESSION_STORAGE


def default_session_repo() -> SessionRepo:
    """Return the shared production JSONL cross-session repository."""
    return DEFAULT_SESSION_REPO


# Imported last: SessionManager reads the DEFAULT_* singletons above (lazily, in
# its constructor), so this import must follow their definition. SessionManager
# itself imports only session submodules, so there is no import cycle.
from core.agent_harness.session.manager import SessionManager  # noqa: E402

__all__ = [
    "CHAT_KINDS",
    "DEFAULT_SESSION_REPO",
    "DEFAULT_SESSION_STORAGE",
    "InMemorySessionStorage",
    "InterventionKind",
    "JsonlSessionRepo",
    "JsonlSessionStorage",
    "Session",
    "SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST",
    "SessionManager",
    "SessionPersistenceSource",
    "SessionRepo",
    "SessionStorage",
    "TerminalMetrics",
    "TerminalMetricsSnapshot",
    "default_session_repo",
    "default_session_storage",
]

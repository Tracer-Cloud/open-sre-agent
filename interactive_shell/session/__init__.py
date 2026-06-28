"""Canonical home for interactive-shell session state, context, and persistence.

This package centralizes everything about a REPL session in one place:

- :class:`ReplSession` — the in-memory session domain object (``state``).
- :class:`ReplRuntimeContext` and :func:`create_repl_runtime_context` — the
  validated runtime bundle assembled for the controller (``context``).
- :class:`SessionStorage` / :class:`SessionRepo` protocols plus their JSONL and
  in-memory backends — persistence, split into per-session writes (storage) and
  cross-session queries (repo).

``ReplSession`` delegates all persistence to an injected ``SessionStorage`` so
the on-disk format is swappable and tests can run without touching the
filesystem. The module-level ``DEFAULT_SESSION_STORAGE`` / ``DEFAULT_SESSION_REPO``
singletons provide the production JSONL backends used by the slash commands.
"""

from __future__ import annotations

from interactive_shell.session.context import (
    ReplRuntimeContext,
    ReplSessionBootstrapSpec,
    create_repl_runtime_context,
    prepare_repl_session,
)
from interactive_shell.session.repo import JsonlSessionRepo
from interactive_shell.session.state import (
    SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
    InterventionKind,
    ReplSession,
    TerminalMetricsSnapshot,
)
from interactive_shell.session.storage import (
    InMemorySessionStorage,
    JsonlSessionStorage,
)
from interactive_shell.session.types import (
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


__all__ = [
    "CHAT_KINDS",
    "DEFAULT_SESSION_REPO",
    "DEFAULT_SESSION_STORAGE",
    "InMemorySessionStorage",
    "InterventionKind",
    "JsonlSessionRepo",
    "JsonlSessionStorage",
    "ReplRuntimeContext",
    "ReplSession",
    "ReplSessionBootstrapSpec",
    "SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST",
    "SessionPersistenceSource",
    "SessionRepo",
    "SessionStorage",
    "TerminalMetricsSnapshot",
    "create_repl_runtime_context",
    "default_session_repo",
    "default_session_storage",
    "prepare_repl_session",
]

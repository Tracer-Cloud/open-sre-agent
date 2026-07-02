"""Centralized session lifecycle owner for every surface.

``SessionManager`` is the single component that creates, resolves, rotates,
restores, and flushes :class:`Session` objects. Surfaces (interactive
shell, gateway, headless) delegate session lifecycle to it instead of each
re-implementing bootstrap + persistence wiring:

- **create** — a fresh session: construct, run the core bootstrap (persistent
  tasks + integration hydration/warm), and open its storage stream.
- **resolve** — load a persisted session by id: construct with that id, run the
  core bootstrap, restore its saved conversation context, and reopen storage.
- **rotate** — flush the outgoing session and create a replacement.
- **restore_context** — rehydrate messages / accumulated context / history from
  a persisted session dict.
- **flush** — write a session's buffered state to storage.

Surface-specific concerns stay with the surface: the shell layers terminal UI
state (theme, grounding providers, prompt history) on top of a manager-created
session; the gateway injects per-chat metadata. Neither re-implements the core
bootstrap, and neither reaches across surfaces to do it.
"""

from __future__ import annotations

import logging
from typing import Any

# Import from submodules (not the package __init__) so the session package can
# re-export SessionManager without a circular import.
from core.agent_harness.session.state import Session
from core.agent_harness.session.tasks import TaskRegistry
from core.agent_harness.session.types import SessionRepo, SessionStorage

logger = logging.getLogger(__name__)


class SessionManager:
    """Owns the create / resolve / rotate / restore / flush session lifecycle.

    Storage and repo backends are injectable so tests can run against in-memory
    persistence; production surfaces use the shared JSONL singletons (resolved
    lazily to avoid importing the package ``__init__`` from within it).
    """

    def __init__(
        self,
        *,
        storage: SessionStorage | None = None,
        repo: SessionRepo | None = None,
    ) -> None:
        if storage is None or repo is None:
            from core.agent_harness.session import (
                DEFAULT_SESSION_REPO,
                DEFAULT_SESSION_STORAGE,
            )

            storage = storage or DEFAULT_SESSION_STORAGE
            repo = repo or DEFAULT_SESSION_REPO
        self._storage = storage
        self._repo = repo

    # ─── Core bootstrap ──────────────────────────────────────────────────

    def bootstrap(
        self,
        session: Session,
        *,
        hydrate_integrations: bool = True,
        warm_integrations: bool = False,
        persistent_tasks: bool = True,
    ) -> Session:
        """Apply the surface-agnostic startup mutations to ``session``.

        This is the single definition of "a booted session": a persistent task
        registry and hydrated (optionally warmed) integration state. Surface UI
        wiring is layered by the surface after this returns.
        """
        if persistent_tasks:
            session.task_registry = TaskRegistry.persistent()
        if hydrate_integrations:
            session.hydrate_configured_integrations()
        if warm_integrations:
            session.warm_resolved_integrations()
        return session

    # ─── Lifecycle ───────────────────────────────────────────────────────

    def create(
        self,
        *,
        session_id: str | None = None,
        hydrate_integrations: bool = True,
        warm_integrations: bool = False,
        persistent_tasks: bool = True,
        open_storage: bool = True,
    ) -> Session:
        """Build a fresh session, bootstrap it, and open its storage stream."""
        session = Session(session_id=session_id) if session_id else Session()
        # Align the session's own persistence backend with the manager's, so
        # session.record()/append go through the same storage the manager opens
        # and flushes. Otherwise an injected backend is bypassed by the default
        # JSONL field on Session.
        session.storage = self._storage
        self.bootstrap(
            session,
            hydrate_integrations=hydrate_integrations,
            warm_integrations=warm_integrations,
            persistent_tasks=persistent_tasks,
        )
        if open_storage:
            self._storage.open_session(session)
        return session

    def resolve(
        self,
        session_id: str,
        *,
        hydrate_integrations: bool = True,
        warm_integrations: bool = True,
        persistent_tasks: bool = True,
    ) -> Session:
        """Load a persisted session by id: bootstrap, restore context, reopen storage."""
        session = self.create(
            session_id=session_id,
            hydrate_integrations=hydrate_integrations,
            warm_integrations=warm_integrations,
            persistent_tasks=persistent_tasks,
            open_storage=False,
        )
        data = self._repo.load_session(session_id)
        self.restore_context(session, data)
        self._storage.reopen_session(session.session_id)
        return session

    def rotate(
        self,
        *,
        old_session_id: str | None = None,
        new_session_id: str | None = None,
        warm_integrations: bool = True,
    ) -> Session:
        """Close the outgoing session (if any) and create its replacement."""
        if old_session_id:
            self.close(Session(session_id=old_session_id))
        return self.create(session_id=new_session_id, warm_integrations=warm_integrations)

    def restore_context(self, session: Session, data: dict[str, Any] | None) -> Session:
        """Rehydrate conversation messages, accumulated context, and history.

        ``data`` is the persisted session dict from ``SessionRepo.load_session``;
        a ``None`` or empty dict leaves the session untouched.
        """
        if not data:
            return session
        messages = data.get("cli_agent_messages")
        if isinstance(messages, list):
            restored: list[tuple[str, str]] = []
            for item in messages:
                try:
                    role, content = item
                except (TypeError, ValueError):
                    continue
                if role in {"user", "assistant"} and isinstance(content, str) and content:
                    restored.append((role, content))
            session.cli_agent_messages = restored
        context = data.get("accumulated_context")
        if isinstance(context, dict):
            session.accumulated_context = dict(context)
        history = data.get("history")
        if isinstance(history, list):
            session.history = [dict(item) for item in history if isinstance(item, dict)]
        return session

    def close(self, session: Session) -> None:
        """Finalize a session: persist buffered state and release live resources.

        This is the terminal lifecycle hook — surfaces call it at end of a REPL
        run, before ``/new`` or ``/resume`` swaps sessions, and it backs
        ``rotate``'s outgoing-session teardown. Persisting is best-effort (a
        failed flush must not crash teardown); resource release prevents
        per-session leaks (cancels the in-flight integration-warm task and
        drops background references).
        """
        try:
            self._storage.flush(session)
        except OSError:
            logger.debug("[session] flush failed during close", exc_info=True)
        self._release_resources(session)

    @staticmethod
    def _release_resources(session: Session) -> None:
        """Cancel background work and drop references so a closed session is collectable."""
        warm_task = getattr(session, "_integration_warm_task", None)
        if warm_task is not None and not warm_task.done():
            warm_task.cancel()
        session._integration_warm_task = None
        session.background_notices.clear()
        session.prompt_refresh_fn = None


__all__ = ["SessionManager"]

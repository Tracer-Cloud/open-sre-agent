"""Per-session integration state, cache helpers, and turn-time resolution.

Owns everything session-scoped for integration discovery: configured service
names, the resolved-config cache, repository scopes, background warm tasks, and
``resolve_and_cache_integrations`` for the turn engine.

``SessionCore`` composes :class:`IntegrationState` as ``session.integrations`` and
re-exposes public fields via properties for API stability. Port-level fetch/classify
logic lives in :mod:`platform.harness_ports` (wired at startup from
``integrations/harness_adapters``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from platform.harness_ports import (
    IntegrationResolutionResult,
    resolve_integrations,
)

if TYPE_CHECKING:
    from core.agent_harness.ports import SessionStore

__all__ = [
    "IntegrationResolutionResult",
    "IntegrationState",
    "has_only_underscore_prefixed_keys",
    "has_resolved_integrations",
    "merge_resolved_integrations",
    "resolve_and_cache_integrations",
    "resolve_integrations",
]


# ---------------------------------------------------------------------------
# Cache helpers (shared by IntegrationState and resolve_and_cache_integrations)
# ---------------------------------------------------------------------------


def has_resolved_integrations(cache: dict[str, Any] | None) -> bool:
    """Return True when the cache holds at least one integration config."""
    if not cache:
        return False
    return any(not str(key).startswith("_") for key in cache)


def has_only_underscore_prefixed_keys(cache: dict[str, Any] | None) -> bool:
    """True when every cache key starts with ``_`` (book-keeping only, no configs)."""
    if not cache:
        return False
    return all(str(key).startswith("_") for key in cache)


def merge_resolved_integrations(
    base: dict[str, Any] | None,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge integration configs while preserving gateway/runtime metadata keys."""
    merged = dict(base or {})
    merged.update(updates)
    return merged


def _has_usable_cache(cache: dict[str, Any] | None) -> bool:
    """True when a cache holds resolved configs and need not be re-resolved."""
    return cache is not None and (
        has_resolved_integrations(cache) or not has_only_underscore_prefixed_keys(cache)
    )


def resolve_and_cache_integrations(session: SessionStore) -> dict[str, Any]:
    """Resolve a session's integration configs, using and updating its cache."""
    cached = session.resolved_integrations_cache
    if _has_usable_cache(cached):
        return dict(cached or {})

    resolved = resolve_integrations()
    if resolved:
        session.resolved_integrations_cache = merge_resolved_integrations(cached, resolved)
    return dict(session.resolved_integrations_cache or {})


# ---------------------------------------------------------------------------
# Session integration facet
# ---------------------------------------------------------------------------


@dataclass
class IntegrationState:
    """A session's integration-resolution state and the logic that warms it."""

    configured: tuple[str, ...] = ()
    """Session-scoped configured integration names for planning-time capability checks."""
    configured_known: bool = False
    """Whether ``configured`` reflects known state (vs default unknown)."""
    resolved_cache: dict[str, Any] | None = None
    """Resolved integration configs (env/store) shared across turns.

    Populated silently at REPL boot and again after integration mutations so the
    conversational assistant and investigations can call registered tools without
    waiting for the first user message to trigger a visible "Loading integrations"
    pass. Cleared by :meth:`refresh` when integrations change."""
    github_repo_scope: tuple[str, str] | None = None
    """Sticky owner/repo inferred from chat, env, or git remote for GitHub tools."""
    gitlab_repo_scope: tuple[str, str, str] | None = None
    """Sticky project/ref/file inferred from chat, env, or git remote for GitLab tools."""

    _warm_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _warm_generation: int = field(default=0, repr=False, compare=False)
    _warm_task: Any = field(default=None, repr=False, compare=False)

    def hydrate(self) -> None:
        """Load configured integration names (env + local store) — metadata only.

        Run at REPL boot and again whenever an integration is added or removed so
        capability checks and the tool-gathering pass reflect the current store
        state instead of a stale boot-time snapshot. Must not resolve keyring-backed
        secrets; full configs are resolved on demand via :meth:`warm`/:meth:`get`.
        """
        try:
            from platform.harness_ports import configured_integration_services

            self.configured = tuple(sorted(configured_integration_services()))
            self.configured_known = True
        except Exception:
            # Best-effort: keep whatever state we already had (default unknown).
            pass

    def warm(self, *, generation: int | None = None) -> None:
        """Resolve full integration configs once, without progress UI.

        Empty resolves are not cached so a later turn can retry if boot-time
        resolution raced store/env hydration. Failures leave the cache unset.
        """
        cached = self.resolved_cache
        if cached is not None and not has_only_underscore_prefixed_keys(cached):
            return
        if generation is None:
            with self._warm_lock:
                generation = self._warm_generation
        try:
            resolved = resolve_integrations()
        except Exception:
            # Best-effort warmup: leave cache unset so later turns can retry.
            return
        self._store(resolved, generation=generation)

    def _store(self, resolved: dict[str, Any], *, generation: int) -> None:
        if not resolved:
            return
        with self._warm_lock:
            if generation != self._warm_generation:
                return
            if self.resolved_cache is not None and not has_only_underscore_prefixed_keys(
                self.resolved_cache
            ):
                return
            self.resolved_cache = merge_resolved_integrations(self.resolved_cache, resolved)

    def get(self) -> IntegrationResolutionResult:
        """Return the session's integration configs as a typed snapshot (cache-aware).

        An explicit empty cache is treated as known state; metadata-only caches
        trigger one quiet warmup, merged through the same generation guard as startup.
        """
        cached = self.resolved_cache
        if _has_usable_cache(cached):
            return IntegrationResolutionResult(resolved_integrations=dict(cached or {}))
        self.warm()
        return IntegrationResolutionResult(resolved_integrations=dict(self.resolved_cache or {}))

    def refresh(self) -> None:
        """Re-resolve after the local store changes: drop cache, re-hydrate, re-warm."""
        self._cancel_warm(drop_cache=True)
        self.hydrate()
        self.warm()

    def reset(self) -> None:
        """Reset all resolution state for /new (cancels any in-flight warm task)."""
        self._cancel_warm(drop_cache=True)
        self.configured = ()
        self.configured_known = False

    def release(self) -> None:
        """Cancel the in-flight warm task for teardown (keeps cached data)."""
        self._cancel_warm(drop_cache=False)

    def _cancel_warm(self, *, drop_cache: bool) -> None:
        with self._warm_lock:
            self._warm_generation += 1
            pending = self._warm_task
            self._warm_task = None
            if drop_cache:
                self.resolved_cache = None
                self.github_repo_scope = None
                self.gitlab_repo_scope = None
        if pending is not None and not pending.done():
            pending.cancel()

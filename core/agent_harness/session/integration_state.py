"""Per-session integration-resolution state and the logic that warms it.

Groups the integration concern that ``SessionCore`` used to carry inline: the
configured integration names, the resolved-config cache, the GitHub repo scope,
and the background warm task (with its generation guard). ``SessionCore`` composes
this as ``session.integrations`` and re-exposes the public fields through properties
for API stability, so this module is the single owner of the coupling to the
``integrations`` domain.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.agent_harness.integrations.resolution_cache import (
    has_only_runtime_metadata,
    has_resolved_integrations,
    merge_resolved_integrations,
)

if TYPE_CHECKING:
    from core.agent_harness.integrations.resolution import IntegrationResolutionResult


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
        if cached is not None and not has_only_runtime_metadata(cached):
            return
        if generation is None:
            with self._warm_lock:
                generation = self._warm_generation
        try:
            from core.agent_harness.integrations.resolution import resolve_integrations

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
            if self.resolved_cache is not None and not has_only_runtime_metadata(
                self.resolved_cache
            ):
                return
            self.resolved_cache = merge_resolved_integrations(self.resolved_cache, resolved)

    def get(self) -> IntegrationResolutionResult:
        """Return the session's integration configs as a typed snapshot (cache-aware).

        An explicit empty cache is treated as known state; metadata-only caches
        trigger one quiet warmup, merged through the same generation guard as startup.
        """
        from core.agent_harness.integrations.resolution import IntegrationResolutionResult

        cached = self.resolved_cache
        if cached is not None and (
            has_resolved_integrations(cached) or not has_only_runtime_metadata(cached)
        ):
            return IntegrationResolutionResult(resolved_integrations=dict(cached))
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
        if pending is not None and not pending.done():
            pending.cancel()

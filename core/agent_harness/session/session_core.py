"""Core session state shared by every surface.

The surface-agnostic half of the REPL session: identity, persistence, integration
resolution, token accounting, conversational agent state, and grounding caches —
everything ``core``, ``gateway``, and ``tools`` consumers depend on. The interactive
shell extends this with its own UI state in
:class:`~core.agent_harness.session.session.Session`.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.agent_harness.grounding.context import GroundingContext
    from core.agent_harness.integrations.resolution import IntegrationResolutionResult
else:
    GroundingContext = Any

from config.llm_reasoning_effort import ReasoningEffortChoice
from core.agent_harness.integrations.resolution_cache import (
    has_only_runtime_metadata,
    has_resolved_integrations,
    merge_resolved_integrations,
)
from core.agent_harness.session.persistence_ports import SessionStorage
from core.agent_harness.session.storage.jsonl import JsonlSessionStorage
from core.agent_harness.session.task_registry import TaskRegistry
from core.agent_harness.session.token_usage import TokenUsage
from core.state import MutableAgentState


def _default_grounding() -> GroundingContext:
    """Build a fresh per-session grounding cache bundle.

    Imported lazily so the session package can expose the state model without
    eagerly constructing grounding caches.
    """
    from core.agent_harness.grounding.context import GroundingContext

    return GroundingContext()


@dataclass
class SessionCore:
    """Surface-agnostic session state accumulated across REPL turns.

    Carries everything we want to persist across individual investigations
    within the same session: previous investigation state (for follow-up
    questions), accumulated infra context (service names, clusters observed),
    and a short interaction history for /status.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Stable UUID for this session. Rotated on /new so each logical session gets its own ID."""

    started_at: float = field(default_factory=time.time)
    """Unix timestamp of when this session (or post-reset sub-session) began."""

    storage: SessionStorage = field(default_factory=JsonlSessionStorage, repr=False, compare=False)
    """Persistence backend for this session's turns and RCA records.

    Defaults to the JSONL backend; tests can inject an in-memory backend. All
    of this session's writes (record/append/flush) go through it, so the on-disk
    format is swappable without touching Session."""

    resumed_from_name: str = ""
    """Name of the most recently resumed session. Used by /sessions to display a
    fallback name for the current session before it has its own first turn."""

    history: list[dict[str, Any]] = field(default_factory=list)
    """Each entry has type, text, and ok fields for shell, slash, alert, and chat turns."""

    last_state: dict[str, Any] | None = None
    """The final AgentState from the most recent investigation, used by follow-ups."""

    last_investigation_id: str = ""
    """Most recent investigation lifecycle id for joining terminal turns to PostHog."""

    last_assistant_intent: str | None = None
    """Intent label set by the runtime after each handled turn.

    Values: "slash", "investigation", "follow_up", and the three
    shell action-agent turn paths: "cli_agent_summarized" (a successful action's
    discovery output was summarized into an answer), "cli_agent_handled" (the
    action fully handled the turn; no LLM answer), and "cli_agent_fallback"
    (nothing handled, gathered evidence and answered via LLM chat).
    """

    configured_integrations: tuple[str, ...] = ()
    """Session-scoped configured integration names for planning-time capability checks."""
    configured_integrations_known: bool = False
    """Whether configured_integrations reflects known state (vs default unknown)."""
    resolved_integrations_cache: dict[str, Any] | None = None
    """Resolved integration configs (env/store) shared across turns.

    Populated silently at REPL boot and again after integration mutations so the
    conversational assistant and investigations can call registered tools without
    waiting for the first user message to trigger a visible "Loading
    integrations" pass. Cleared by ``refresh_integration_state`` when
    integrations change."""
    github_repo_scope: tuple[str, str] | None = None
    """Sticky owner/repo inferred from chat, env, or git remote for GitHub tools."""
    _integration_warm_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )
    _integration_warm_generation: int = field(default=0, repr=False, compare=False)
    _integration_warm_task: Any = field(default=None, repr=False, compare=False)
    available_capabilities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Optional planning-time capability constraints (slash/cli/synthetic)."""

    accumulated_context: dict[str, Any] = field(default_factory=dict)
    """Reusable infra context — service names, clusters, regions — learned from
    earlier investigations that should seed future ones."""

    reasoning_effort: ReasoningEffortChoice | None = None
    """Session-scoped reasoning effort preference for REPL-driven LLM calls."""

    tokens: TokenUsage = field(default_factory=TokenUsage)
    """Per-session token accounting (running totals + LLM call count) for ``/cost``."""

    task_registry: TaskRegistry = field(default_factory=TaskRegistry)
    """This session's in-flight and completed tasks (for /tasks and /cancel).

    Session-scoped task state whose lifecycle the manager owns (bootstrap swaps in
    a persistent registry); only the shell surface reads it today."""

    agent: MutableAgentState = field(default_factory=MutableAgentState)
    """Dedicated conversational-agent state (transcript + per-turn observation).

    Owns the assistant conversation history (alternating
    (\"user\"|\"assistant\", text)) and the per-turn read-only discovery
    observation, kept in one place rather than as loose session fields."""

    grounding: GroundingContext = field(
        default_factory=_default_grounding, repr=False, compare=False
    )
    """Per-session LLM grounding caches (CLI help, docs, AGENTS.md).

    Injected so the grounding caches have a process-scoped lifetime with no
    module-level mutable globals; tests can supply a fresh ``GroundingContext``."""

    last_synthetic_observation_path: str | None = None
    """Absolute path to ``latest.json`` for the last finished synthetic run (set on failure)."""

    # Infra keys pulled from a completed investigation state and carried into the
    # next investigation. A class-level tuple so callers have a single source for
    # "what counts as accumulated context".
    _ACCUMULATED_KEYS: tuple[str, ...] = (
        "service",
        "pipeline_name",
        "cluster_name",
        "region",
        "environment",
    )

    @property
    def cli_agent_messages(self) -> list[tuple[str, str]]:
        """Compatibility view used by the surface-agnostic agent turn engine."""
        return self.agent.messages

    @cli_agent_messages.setter
    def cli_agent_messages(self, value: list[tuple[str, str]]) -> None:
        self.agent.messages = value

    @property
    def last_command_observation(self) -> str | None:
        """Latest command/tool observation for the current turn."""
        return self.agent.last_observation

    @last_command_observation.setter
    def last_command_observation(self, value: str | None) -> None:
        self.agent.last_observation = value

    def _bind_last_synthetic_observation(self, scenario_id: str) -> None:
        if not scenario_id:
            self.last_synthetic_observation_path = None
            return
        # Shared path constant lives in config so core and surfaces stay decoupled.
        try:
            from config.constants.paths import SYNTHETIC_SCENARIOS_DIR
        except Exception:
            self.last_synthetic_observation_path = None
            return
        latest = SYNTHETIC_SCENARIOS_DIR / "_observations" / scenario_id / "latest.json"
        for _ in range(8):
            if latest.is_file():
                self.last_synthetic_observation_path = str(latest.resolve())
                return
            time.sleep(0.06)
        self.last_synthetic_observation_path = None

    def record(
        self,
        kind: str,
        text: str,
        *,
        ok: bool = True,
        response_text: str | None = None,
        slash_outcome: str | None = None,
    ) -> None:
        """Append an entry to the session history.

        Supports kinds: "shell", "slash", "alert", "chat", "incoming_alert", etc.
        For "incoming_alert", use record_incoming_alert() instead to preserve metadata.

        ``slash_outcome`` tags typo-style slash failures (for example
        ``unknown_command`` or ``invalid_subcommand``) so analytics can
        distinguish them from handler failures.
        """
        entry: dict[str, Any] = {"type": kind, "text": text, "ok": ok}
        if response_text:
            entry["response_text"] = response_text
        if slash_outcome:
            entry["slash_outcome"] = slash_outcome

        self.history.append(entry)

        self.storage.append_turn(self, kind, text)

    def mark_latest(self, *, ok: bool, kind: str | None = None) -> None:
        """Update the latest history entry, optionally scanning for a matching kind."""
        for latest in reversed(self.history):
            if kind is not None and latest.get("type") != kind:
                continue
            latest["ok"] = ok
            return

    def complete_latest_record(
        self,
        kind: str,
        *,
        response_text: str | None = None,
        ok: bool | None = None,
        slash_outcome: str | None = None,
    ) -> None:
        """Update the newest history row of ``kind`` with analytics outcome text."""
        for latest in reversed(self.history):
            if latest.get("type") != kind:
                continue
            if ok is not None:
                latest["ok"] = ok
            if slash_outcome:
                latest["slash_outcome"] = slash_outcome
            if response_text and response_text.strip():
                latest["response_text"] = response_text.strip()
            return

    def accumulate_from_state(self, state: dict[str, Any] | None) -> None:
        """Extract reusable infra hints from a completed investigation state.

        Called after every successful investigation (whether triggered by
        free-text input or by the ``/investigate`` slash command) so that
        subsequent investigations within the same REPL session inherit the
        service / cluster / region context discovered earlier.
        """
        if not state:
            return
        for key in self._ACCUMULATED_KEYS:
            value = state.get(key)
            if value:
                self.accumulated_context[key] = value

    def hydrate_configured_integrations(self) -> None:
        """Load configured integration names (env + local store) onto the session.

        Run at REPL boot and again whenever an integration is added or removed
        so capability checks and the tool-gathering pass reflect the current
        store state instead of a stale boot-time snapshot. This startup path is
        intentionally metadata-only: it must not resolve keyring-backed secrets.
        Full integration configs are resolved on demand when a turn needs tools
        or an investigation starts.
        """
        try:
            from platform.harness_ports import configured_integration_services

            self.configured_integrations = tuple(sorted(configured_integration_services()))
            self.configured_integrations_known = True
        except Exception:
            # Best-effort: keep whatever state we already had (default unknown).
            pass

    def warm_resolved_integrations(self, *, generation: int | None = None) -> None:
        """Resolve full integration configs once, without progress UI.

        The banner already shows configured integration names from
        :meth:`hydrate_configured_integrations`; this loads the classified configs
        the tool-gathering pass and investigation pipeline need so the first
        conversational turn does not pay resolve cost or emit READ progress.

        Empty resolves are not cached so a later turn can retry if boot-time
        resolution raced store/env hydration. Failures leave the cache unset for
        the same reason.
        """
        cached = self.resolved_integrations_cache
        if cached is not None and not has_only_runtime_metadata(cached):
            return
        if generation is None:
            with self._integration_warm_lock:
                generation = self._integration_warm_generation

        try:
            from core.agent_harness.integrations.resolution import resolve_integrations

            resolved = resolve_integrations()
        except Exception:
            # Best-effort warmup: leave cache unset so later turns can retry.
            return

        self._store_warm_cache(resolved, generation=generation)

    def _store_warm_cache(self, resolved: dict[str, Any], *, generation: int) -> None:
        if not resolved:
            return
        with self._integration_warm_lock:
            if generation != self._integration_warm_generation:
                return
            if self.resolved_integrations_cache is not None and not has_only_runtime_metadata(
                self.resolved_integrations_cache
            ):
                return
            self.resolved_integrations_cache = merge_resolved_integrations(
                self.resolved_integrations_cache,
                resolved,
            )

    def get_integrations(self) -> IntegrationResolutionResult:
        """Return this REPL session's integration configs as a typed snapshot.

        The accessor is cache-aware: an explicit empty cache is treated as
        known state, metadata-only caches trigger one quiet warmup attempt, and
        warmup results are merged through the same generation guard as startup.
        """
        from core.agent_harness.integrations.resolution import IntegrationResolutionResult

        cached = self.resolved_integrations_cache
        if cached is not None and (
            has_resolved_integrations(cached) or not has_only_runtime_metadata(cached)
        ):
            return IntegrationResolutionResult(resolved_integrations=dict(cached))

        self.warm_resolved_integrations()
        return IntegrationResolutionResult(
            resolved_integrations=dict(self.resolved_integrations_cache or {})
        )

    def schedule_warm_resolved_integrations(self) -> None:
        """Warm integration configs off the interactive prompt critical path."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.warm_resolved_integrations()
            return

        with self._integration_warm_lock:
            if self._integration_warm_task is not None and not self._integration_warm_task.done():
                return
            generation = self._integration_warm_generation

        async def _run_warm() -> None:
            await asyncio.to_thread(self.warm_resolved_integrations, generation=generation)

        task = loop.create_task(_run_warm())
        with self._integration_warm_lock:
            self._integration_warm_task = task

        def _clear_warm_task(done_task: asyncio.Task[None]) -> None:
            with self._integration_warm_lock:
                if self._integration_warm_task is done_task:
                    self._integration_warm_task = None

        task.add_done_callback(_clear_warm_task)

    def refresh_integration_state(self) -> None:
        """Re-resolve integration state after the local store changes.

        Drops the cached resolution (``resolved_integrations_cache``) and
        re-hydrates ``configured_integrations`` from the current env + store
        set. Call after a ``/integrations setup|remove`` or
        ``/mcp connect|disconnect`` mutates the local store so the same REPL
        session immediately reflects the change instead of answering from the
        boot-time snapshot.
        """
        with self._integration_warm_lock:
            self._integration_warm_generation += 1
            pending = self._integration_warm_task
            self._integration_warm_task = None
            self.resolved_integrations_cache = None
            self.github_repo_scope = None
        if pending is not None and not pending.done():
            pending.cancel()
        self.hydrate_configured_integrations()
        self.warm_resolved_integrations()

    def apply_investigation_result(
        self,
        state: dict[str, Any],
        *,
        trigger: str = "",
    ) -> None:
        """Record a completed investigation result.

        Replaces the inline ``session.last_state = …`` +
        ``session.accumulate_from_state(…)`` pattern at every call site so the
        last-state update and accumulated-context update stay in one place.
        """
        self.last_state = state
        self.accumulate_from_state(state)
        self.storage.append_investigation_result(self.session_id, state, trigger=trigger)

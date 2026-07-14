"""Core session state shared by every surface.

The surface-agnostic half of the REPL session: identity, persistence, integration
resolution, token accounting, conversational agent state, and grounding caches —
everything ``core``, ``gateway``, and ``tools`` consumers depend on. The interactive
shell extends this with its own UI state in
:class:`~surfaces.interactive_shell.session.session.Session`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.agent_harness.grounding.context import GroundingContext
    from core.agent_harness.session.integration_resolution import IntegrationResolutionResult
else:
    GroundingContext = Any

from config.llm_reasoning_effort import ReasoningEffortChoice
from core.agent_harness.accounting.token_usage import TokenUsage
from core.agent_harness.session.integration_resolution import IntegrationState
from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from core.agent_harness.session.persistence.ports import SessionStorage
from core.state import MutableAgentState
from platform.common.task_registry import TaskRegistry


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

    integrations: IntegrationState = field(default_factory=IntegrationState)
    """Integration-resolution state: configured names, resolved-config cache, warm task.

    The public fields are re-exposed as properties below for API stability; the
    resolution logic and the coupling to the ``integrations`` domain live on
    ``IntegrationState``."""
    available_capabilities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Optional planning-time capability constraints (slash/cli/synthetic)."""

    accumulated_context: dict[str, Any] = field(default_factory=dict)
    """Reusable infra context — service names, clusters, regions — learned from
    earlier investigations that should seed future ones."""

    runtime_metadata: dict[str, Any] = field(default_factory=dict)
    """Read-only process facts (version, build, env) exposed to prompts and sandboxed tools."""

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

    # ── integration state: public fields re-exposed from the composed IntegrationState ──

    @property
    def configured_integrations(self) -> tuple[str, ...]:
        """Session-scoped configured integration names for planning-time capability checks."""
        return self.integrations.configured

    @configured_integrations.setter
    def configured_integrations(self, value: tuple[str, ...]) -> None:
        self.integrations.configured = value

    @property
    def configured_integrations_known(self) -> bool:
        """Whether ``configured_integrations`` reflects known state (vs default unknown)."""
        return self.integrations.configured_known

    @configured_integrations_known.setter
    def configured_integrations_known(self, value: bool) -> None:
        self.integrations.configured_known = value

    @property
    def resolved_integrations_cache(self) -> dict[str, Any] | None:
        """Resolved integration configs (env/store) shared across turns."""
        return self.integrations.resolved_cache

    @resolved_integrations_cache.setter
    def resolved_integrations_cache(self, value: dict[str, Any] | None) -> None:
        self.integrations.resolved_cache = value

    @property
    def github_repo_scope(self) -> tuple[str, str] | None:
        """Sticky owner/repo inferred from chat, env, or git remote for GitHub tools."""
        return self.integrations.github_repo_scope

    @github_repo_scope.setter
    def github_repo_scope(self, value: tuple[str, str] | None) -> None:
        self.integrations.github_repo_scope = value

    @property
    def gitlab_repo_scope(self) -> tuple[str, str, str] | None:
        """Sticky project/ref/file inferred from chat, env, or git remote for GitLab tools."""
        return self.integrations.gitlab_repo_scope

    @gitlab_repo_scope.setter
    def gitlab_repo_scope(self, value: tuple[str, str, str] | None) -> None:
        self.integrations.gitlab_repo_scope = value

    def refresh_runtime_metadata(self) -> None:
        """Repopulate :attr:`runtime_metadata` from current process facts."""
        from config.runtime_metadata import build_runtime_metadata

        self.runtime_metadata = build_runtime_metadata()

    def hydrate_configured_integrations(self) -> None:
        """Load configured integration names (env + local store); metadata-only."""
        self.integrations.hydrate()

    def warm_resolved_integrations(self, *, generation: int | None = None) -> None:
        """Resolve full integration configs once, without progress UI."""
        self.integrations.warm(generation=generation)

    def get_integrations(self) -> IntegrationResolutionResult:
        """Return the session's integration configs as a typed snapshot (cache-aware)."""
        return self.integrations.get()

    def refresh_integration_state(self) -> None:
        """Re-resolve integration state after the local store changes."""
        self.integrations.refresh()

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

    def clear(self, *, rotate_identity: bool = True) -> None:
        """Reset core session state to fresh (used by /new and /resume).

        Shell subclasses override to also reset their facets; see
        :meth:`~surfaces.interactive_shell.session.session.Session.clear`.
        """
        self.history.clear()
        self.resumed_from_name = ""
        self.last_state = None
        self.last_assistant_intent = None
        self.integrations.reset()
        self.available_capabilities.clear()
        self.accumulated_context.clear()
        self.tokens.reset()
        self.agent.clear()
        self.refresh_runtime_metadata()
        # Keep persisted cross-session task history on disk intact.
        # /new is session-scoped, so swap in a fresh in-memory registry
        # that reuses the same backing store (if any) so /tasks still shows history.
        persist_path = self.task_registry._persist_path
        self.task_registry = (
            TaskRegistry(persist_path=persist_path, load=False)
            if persist_path is not None
            else TaskRegistry()
        )
        self.last_synthetic_observation_path = None
        if rotate_identity:
            # Rotate session identity so the new post-reset session gets its own ID and file.
            self.session_id = str(uuid.uuid4())
            self.started_at = time.time()

    def release_resources(self) -> None:
        """Cancel background integration-warm work for teardown.

        Called when the handle is discarded (see ``SessionManager.close``); the
        session owns its own teardown. Thread-safe against a background warm
        thread. Shell subclasses override to also drop loop-owned UI references.
        """
        self.integrations.release()

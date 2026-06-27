"""In-memory session state that persists across REPL turns."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict

if TYPE_CHECKING:
    from prompt_toolkit.history import History

    from core.domain.alerts.inbox import IncomingAlert
    from context.grounding.context import GroundingContext

from config.llm_reasoning_effort import ReasoningEffortChoice
from config.strict_config import StrictConfigModel
from context.session.storage.jsonl import JsonlSessionStorage
from context.session.types import SessionStorage
from context.state import MutableAgentState
from interactive_shell.runtime.background.models import (
    BackgroundInvestigationRecord,
    BackgroundNotificationPreferences,
)
from interactive_shell.runtime.core.tasks import TaskRegistry

InterventionKind = Literal["ctrl_c", "correction"]

# Prefilled into the next prompt after a background synthetic test exits non-zero,
# so the user can ask the CLI assistant for a quick RCA explanation.
SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST = "why did it fail?"

_SCENARIO_FLAG_RE = re.compile(r"--scenario\s+(\S+)")
_SYNTHETIC_SCENARIO_ID_RE = re.compile(r"^\d{3}-[a-z0-9][a-z0-9-]*$")


def _scenario_id_from_synthetic_label(label: str) -> str:
    """Extract a scenario id from a synthetic command or ``suite:scenario`` label."""
    match = _SCENARIO_FLAG_RE.search(label)
    if match is not None:
        candidate = match.group(1).strip()
        return candidate if _SYNTHETIC_SCENARIO_ID_RE.fullmatch(candidate) else ""
    if ":" in label:
        candidate = label.rsplit(":", 1)[-1].strip()
        return candidate if _SYNTHETIC_SCENARIO_ID_RE.fullmatch(candidate) else ""
    return ""


def _default_grounding() -> GroundingContext:
    """Build a fresh per-session grounding cache bundle.

    Imported lazily so the session package keeps a one-way static dependency
    direction (``harness -> session``) and avoids an import cycle.
    """
    from context.grounding.context import GroundingContext

    return GroundingContext()


class TerminalMetricsSnapshot(StrictConfigModel):
    """Session-level aggregate counters for interactive-shell analytics.

    A pure immutable value snapshot returned from ``record_terminal_turn``; the
    mutable session state itself stays a dataclass (it holds a lock and mutates
    per turn).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_index: int
    fallback_count: int
    action_success_percent: float
    fallback_rate_percent: float


@dataclass
class ReplSession:
    """Per-REPL-process accumulated state.

    Carries everything we want to persist across individual investigations
    within the same REPL session: previous investigation state (for follow-up
    questions), accumulated infra context (service names, clusters observed),
    trust mode flag, and a short interaction history for /status.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Stable UUID for this session. Rotated on /new so each logical session gets its own ID."""

    started_at: float = field(default_factory=time.time)
    """Unix timestamp of when this session (or post-reset sub-session) began."""

    storage: SessionStorage = field(default_factory=JsonlSessionStorage, repr=False, compare=False)
    """Persistence backend for this session's turns and RCA records.

    Defaults to the JSONL backend; tests can inject an in-memory backend. All
    of this session's writes (record/append/flush) go through it, so the on-disk
    format is swappable without touching ReplSession."""

    resumed_from_name: str = ""
    """Name of the most recently resumed session. Used by /sessions to display a
    fallback name for the current session before it has its own first turn."""

    history: list[dict[str, Any]] = field(default_factory=list)
    """Each entry has type, text, and ok fields for shell, slash, alert, and chat turns."""

    last_state: dict[str, Any] | None = None
    """The final AgentState from the most recent investigation, used by follow-ups."""

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

    trust_mode: bool = False
    """When True, confirmation prompts for elevated REPL actions are skipped."""

    reasoning_effort: ReasoningEffortChoice | None = None
    """Session-scoped reasoning effort preference for REPL-driven LLM calls."""

    token_usage: dict[str, int] = field(default_factory=dict)
    """Accumulated token counts.

    Totals: ``input``, ``output``. Breakdown: ``input_measured``,
    ``output_measured``, ``input_estimated``, ``output_estimated``.
    """

    llm_call_count: int = 0
    """Number of LLM calls accumulated into ``token_usage`` (for ``/cost``)."""

    agent: MutableAgentState = field(default_factory=MutableAgentState)
    """Dedicated conversational-agent state (transcript + per-turn observation).

    Owns the assistant conversation history (alternating
    (\"user\"|\"assistant\", text)) and the per-turn read-only discovery
    observation, kept in one place rather than as loose session fields."""

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

    prompt_history_backend: History | None = None
    """The live ``prompt_toolkit.History`` object backing the input prompt.

    Stored here so ``/history`` and ``/privacy`` slash commands can mutate
    its ``paused`` flag (when it is a ``RedactingFileHistory``) without
    needing access to the ``PromptSession``."""

    grounding: GroundingContext = field(
        default_factory=_default_grounding, repr=False, compare=False
    )
    """Per-session LLM grounding caches (CLI help, docs, AGENTS.md).

    Injected so the grounding caches have a process-scoped lifetime with no
    module-level mutable globals; tests can supply a fresh ``GroundingContext``."""

    pt_style_app: Any = None
    """The prompt-toolkit ``Application`` instance for this session.

    Stored here (instead of accessed via ``get_app_or_none()``) so that
    worker-thread slash commands (e.g. ``/theme``) can refresh styles via
    ``call_soon_threadsafe`` on the main asyncio loop."""

    main_loop: Any = None
    """The asyncio event loop for the main REPL coroutine.

    Set once by ``InteractiveShellController.start_interactive_shell`` so
    worker-thread code can schedule prompt-toolkit updates on the main thread."""

    active_theme_name: str = "green"
    """Interactive shell palette name for this REPL session (``/theme``, prompts)."""

    pending_theme_refresh: bool = False
    """When True, apply the active palette to prompt-toolkit before the next prompt."""

    task_registry: TaskRegistry = field(default_factory=TaskRegistry)
    """Recent in-flight and completed shell tasks for /tasks and /cancel."""

    background_mode_enabled: bool = False
    """Whether new investigations should run as session-local background tasks."""

    background_investigations: dict[str, BackgroundInvestigationRecord] = field(
        default_factory=dict
    )
    """Completed or in-flight background RCA summaries, keyed by task id."""

    background_notification_preferences: BackgroundNotificationPreferences = field(
        default_factory=BackgroundNotificationPreferences
    )
    """Preferred notification channels for background RCA completion events."""

    background_notices: list[str] = field(default_factory=list)
    """Thread-safe queue of Rich markup messages drained by the REPL main loop."""

    _background_notices_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    history_generation: int = 0
    """Incremented on /new so background synthetic watchers can skip stale history writes."""

    terminal_turn_count: int = 0
    terminal_fallback_count: int = 0
    terminal_actions_executed_count: int = 0
    terminal_actions_success_count: int = 0

    ctrl_c_intervention_count: int = 0
    """Incremented when the user Ctrl-Cs an active investigation. Bare-prompt
    Ctrl-C with no agent running is intentionally not counted."""

    correction_intervention_count: int = 0
    """Incremented when a follow-up or new-alert message starts with a
    correction cue (see ``looks_like_correction`` in ``runtime.core.turn_detection``).
    Slash and CLI-agent turns are not counted because content like
    ``actually run ps aux`` is a command, not a correction."""

    pending_prompt_default: str | None = None
    """When set, the next interactive prompt is pre-filled with this string (then cleared)."""

    pending_prompt_autosubmit: bool = False
    """When True alongside ``pending_prompt_default``, the prefilled prompt is
    submitted automatically instead of waiting for the user to press Enter.

    Used to auto-launch an interactive command the agent decided to run (e.g.
    ``/integrations setup sentry``) so it flows through the normal
    exclusive-stdin dispatch path — the only place an interactive child process
    gets clean stdin."""

    prompt_refresh_fn: Callable[[], None] | None = field(default=None, repr=False)
    """Loop-owned hook to apply pending prefill and redraw the active prompt."""

    last_synthetic_observation_path: str | None = None
    """Absolute path to ``latest.json`` for the last finished synthetic run (set on failure)."""

    incoming_alerts: list[IncomingAlert] = field(default_factory=list)
    """Queued incoming alerts from the HTTP listener, capped at 256 entries.
    Shows up in /status and /history for user visibility."""

    _INCOMING_ALERTS_MAX: int = 256
    """Maximum number of incoming alerts to keep in session history."""
    # the next investigation.  Kept as a class-level tuple so any caller that
    # wants to know "what counts as accumulated context" has a single source.
    _ACCUMULATED_KEYS: tuple[str, ...] = (
        "service",
        "pipeline_name",
        "cluster_name",
        "region",
        "environment",
    )

    def take_pending_prompt_default(self) -> str:
        """Return pre-filled text for the next prompt line, if any, and clear it."""
        value = self.pending_prompt_default
        self.pending_prompt_default = None
        return value or ""

    def take_pending_autosubmit(self) -> bool:
        """Return whether the pending prefill should auto-submit, and clear the flag."""
        value = self.pending_prompt_autosubmit
        self.pending_prompt_autosubmit = False
        return value

    def queue_auto_command(self, command: str) -> None:
        """Queue a command to run automatically on the next prompt iteration.

        Prefills the input with ``command`` and marks it for auto-submit, then
        refreshes the active prompt so the loop submits it without waiting for
        Enter. Lets the agent launch an interactive command (setup/connect)
        through the normal exclusive-stdin dispatch path rather than spawning it
        mid-turn, where it would fight the live prompt for stdin.
        """
        self.pending_prompt_default = command
        self.pending_prompt_autosubmit = True
        self.notify_prompt_changed()

    def notify_prompt_changed(self) -> None:
        """Redraw the active prompt (placeholder state and pending prefill)."""
        if self.prompt_refresh_fn is not None:
            self.prompt_refresh_fn()

    def enqueue_background_notice(self, message: str) -> None:
        """Queue a background-thread status line for the main REPL loop to print."""
        with self._background_notices_lock:
            self.background_notices.append(message)
        self.notify_prompt_changed()

    def drain_background_notices(self) -> list[str]:
        """Return and clear any queued background status lines."""
        with self._background_notices_lock:
            notices = list(self.background_notices)
            self.background_notices.clear()
        return notices

    def suggest_synthetic_failure_follow_up(self, *, label: str = "") -> None:
        """Queue RCA prefill after a failed synthetic run and refresh the active prompt."""
        self.pending_prompt_default = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST
        self.notify_prompt_changed()
        self._bind_last_synthetic_observation(_scenario_id_from_synthetic_label(label))
        self.notify_prompt_changed()

    def _bind_last_synthetic_observation(self, scenario_id: str) -> None:
        if not scenario_id:
            self.last_synthetic_observation_path = None
            return
        try:
            from cli.tests.discover import SYNTHETIC_SCENARIOS_DIR
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

    @property
    def token_usage_has_estimates(self) -> bool:
        usage = self.token_usage
        return bool(usage.get("input_estimated") or usage.get("output_estimated"))

    def record_token_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated: bool = False,
    ) -> None:
        """Accumulate token counts for ``/cost`` (input/output keys)."""
        if not input_tokens and not output_tokens:
            return
        suffix = "estimated" if estimated else "measured"
        for direction, count in (("input", input_tokens), ("output", output_tokens)):
            if not count:
                continue
            self.token_usage[direction] = self.token_usage.get(direction, 0) + count
            bucket = f"{direction}_{suffix}"
            self.token_usage[bucket] = self.token_usage.get(bucket, 0) + count
        self.llm_call_count += 1

    def record(
        self,
        kind: str,
        text: str,
        *,
        ok: bool = True,
        response_text: str | None = None,
    ) -> None:
        """Append an entry to the session history.

        Supports kinds: "shell", "slash", "alert", "chat", "incoming_alert", etc.
        For "incoming_alert", use record_incoming_alert() instead to preserve metadata.
        """
        entry: dict[str, Any] = {"type": kind, "text": text, "ok": ok}
        if response_text:
            entry["response_text"] = response_text

        self.history.append(entry)

        self.storage.append_turn(self, kind, text)

    def record_incoming_alert(self, alert: IncomingAlert) -> None:
        """Append a full IncomingAlert with all metadata to session history.

        Also appends to incoming_alerts list (capped at _INCOMING_ALERTS_MAX).
        This preserves received_at, severity, source, and alert_name metadata
        so that /status displays accurate timestamps and future uses have complete data.
        """
        # Record to history with alert text
        self.history.append({"type": "incoming_alert", "text": alert.text, "ok": True})
        self.storage.append_turn(self, "incoming_alert", alert.text)

        # Store the full alert object to preserve all metadata
        self.incoming_alerts.append(alert)

        # Cap the list at _INCOMING_ALERTS_MAX
        if len(self.incoming_alerts) > self._INCOMING_ALERTS_MAX:
            self.incoming_alerts.pop(0)

    def mark_latest(self, *, ok: bool, kind: str | None = None) -> None:
        """Update the latest history entry, optionally scanning for a matching kind."""
        for latest in reversed(self.history):
            if kind is not None and latest.get("type") != kind:
                continue
            latest["ok"] = ok
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
        """Resolve configured integrations (env + local store) onto the session.

        Run at REPL boot and again whenever an integration is added or removed
        so capability checks and the tool-gathering pass reflect the current
        store state instead of a stale boot-time snapshot. Resolution covers
        both environment variables and the local ``~/.opensre`` store, so an
        integration configured via ``/integrations setup`` (which writes to the
        store) is seen here. Best-effort: any failure leaves the previously
        known state untouched.
        """
        try:
            from integrations.verify import resolve_effective_integrations

            self.configured_integrations = tuple(sorted(resolve_effective_integrations()))
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
        if self.resolved_integrations_cache is not None:
            return
        if generation is None:
            with self._integration_warm_lock:
                generation = self._integration_warm_generation

        try:
            from tools.investigation.stages.resolve_integrations import resolve_integrations_quiet

            resolved = resolve_integrations_quiet({})  # type: ignore[arg-type]
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
            if self.resolved_integrations_cache is not None:
                return
            self.resolved_integrations_cache = dict(resolved)

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

    def clear(self, *, rotate_identity: bool = True) -> None:
        """Reset the session to a fresh state (used by /new and /resume)."""
        self.history_generation += 1
        self.history.clear()
        self.resumed_from_name = ""
        self.last_state = None
        self.last_assistant_intent = None
        self.configured_integrations = ()
        self.configured_integrations_known = False
        with self._integration_warm_lock:
            self._integration_warm_generation += 1
            pending = self._integration_warm_task
            self._integration_warm_task = None
            self.resolved_integrations_cache = None
            self.github_repo_scope = None
        if pending is not None and not pending.done():
            pending.cancel()
        self.available_capabilities.clear()
        self.accumulated_context.clear()
        self.token_usage.clear()
        self.llm_call_count = 0
        self.agent.clear()
        self.incoming_alerts.clear()
        # Keep persisted cross-session task history on disk intact.
        # /new is session-scoped, so swap in a fresh in-memory registry
        # that reuses the same backing store (if any) so /tasks still shows history.
        persist_path = self.task_registry._persist_path
        self.task_registry = (
            TaskRegistry(persist_path=persist_path, load=False)
            if persist_path is not None
            else TaskRegistry()
        )

        self.terminal_turn_count = 0
        self.terminal_fallback_count = 0
        self.terminal_actions_executed_count = 0
        self.terminal_actions_success_count = 0

        self.ctrl_c_intervention_count = 0
        self.correction_intervention_count = 0
        self.pending_prompt_default = None
        self.pending_prompt_autosubmit = False
        self.last_synthetic_observation_path = None
        self.background_mode_enabled = False
        self.background_investigations.clear()
        # Preserve notification channel prefs across /new like trust_mode.
        # Only reset when the user explicitly changes them via /background notify.
        with self._background_notices_lock:
            self.background_notices.clear()
        # trust_mode and reasoning_effort are intentionally preserved across /new
        if rotate_identity:
            # Rotate session identity so the new post-reset session gets its own ID and file.
            self.session_id = str(uuid.uuid4())
            self.started_at = time.time()

    def record_intervention(self, kind: InterventionKind) -> None:
        """Increment the per-kind intervention counter (Ctrl-C or correction)."""
        if kind == "ctrl_c":
            self.ctrl_c_intervention_count += 1
        elif kind == "correction":
            self.correction_intervention_count += 1
        else:
            raise ValueError(f"Unknown intervention kind: {kind!r}")

    def record_terminal_turn(
        self,
        *,
        executed_count: int,
        executed_success_count: int,
        fallback_to_llm: bool,
    ) -> TerminalMetricsSnapshot:
        """Update aggregate terminal metrics and return a stable snapshot."""
        self.terminal_turn_count += 1
        self.terminal_actions_executed_count += max(0, executed_count)
        self.terminal_actions_success_count += max(0, executed_success_count)
        if fallback_to_llm:
            self.terminal_fallback_count += 1
        action_success_percent = (
            100.0 * self.terminal_actions_success_count / self.terminal_actions_executed_count
            if self.terminal_actions_executed_count > 0
            else 0.0
        )
        fallback_rate_percent = 100.0 * self.terminal_fallback_count / self.terminal_turn_count
        return TerminalMetricsSnapshot(
            turn_index=self.terminal_turn_count,
            fallback_count=self.terminal_fallback_count,
            action_success_percent=action_success_percent,
            fallback_rate_percent=fallback_rate_percent,
        )

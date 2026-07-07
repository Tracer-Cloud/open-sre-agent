"""Interactive-shell session: SessionCore plus terminal UI state.

Extends :class:`~core.agent_harness.session.session_core.SessionCore` with the
shell-only facets (``terminal`` UI/background state and the ``alerts`` inbox) and
the methods that drive them.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.session.alert_inbox import SessionAlertInbox
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session.tasks import TaskRegistry
from core.agent_harness.session.terminal_session import TerminalSession
from core.domain.alerts.inbox import IncomingAlert

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


@dataclass
class Session(SessionCore):
    """Per-REPL-process session: :class:`SessionCore` plus interactive-shell state.

    Adds the shell-only ``terminal`` facet (UI/theme/prompt-toolkit/background)
    and the ``alerts`` inbox on top of the surface-agnostic core.
    """

    terminal: TerminalSession = field(default_factory=TerminalSession)
    """Interactive-shell (terminal) session facet — shell-only UI/theme/background state.

    Always present (empty for non-shell sessions) so shell code needs no None-guard;
    ``core``/``gateway``/``tools`` consumers ignore it. Holds the theme, prompt-toolkit,
    pending-prompt/stdin, background-jobs, and metrics/task-registry clusters (#3690)."""

    alerts: SessionAlertInbox = field(default_factory=SessionAlertInbox)
    """Inbox of externally-received alerts (shell alert listener → ``/status``).

    A surface facet: the bounded alert list + cap live on ``SessionAlertInbox`` so
    core-session consumers that never touch alerts don't see the field."""

    def take_pending_prompt_default(self) -> str:
        """Return pre-filled text for the next prompt line, if any, and clear it."""
        value = self.terminal.pending_prompt_default
        self.terminal.pending_prompt_default = None
        return value or ""

    def take_pending_autosubmit(self) -> bool:
        """Return whether the pending prefill should auto-submit, and clear the flag."""
        value = self.terminal.pending_prompt_autosubmit
        self.terminal.pending_prompt_autosubmit = False
        return value

    def queue_auto_command(self, command: str) -> None:
        """Queue a command to run automatically on the next prompt iteration.

        Prefills the input with ``command`` and marks it for auto-submit, then
        refreshes the active prompt so the loop submits it without waiting for
        Enter. Lets the agent launch an interactive command (setup/connect)
        through the normal exclusive-stdin dispatch path rather than spawning it
        mid-turn, where it would fight the live prompt for stdin.
        """
        self.terminal.pending_prompt_default = command
        self.terminal.pending_prompt_autosubmit = True
        self.notify_prompt_changed()

    def notify_prompt_changed(self) -> None:
        """Redraw the active prompt (placeholder state and pending prefill)."""
        if self.terminal.prompt_refresh_fn is not None:
            self.terminal.prompt_refresh_fn()

    def ensure_fleet_sampler_started(self) -> None:
        """Request that the fleet sampler start (no-op if unwired or already running)."""
        if self.terminal.fleet_sampler_starter is not None:
            self.terminal.fleet_sampler_starter()

    def enqueue_background_notice(self, message: str) -> None:
        """Queue a background-thread status line for the main REPL loop to print."""
        with self.terminal._background_notices_lock:
            self.terminal.background_notices.append(message)
        self.notify_prompt_changed()

    def drain_background_notices(self) -> list[str]:
        """Return and clear any queued background status lines."""
        with self.terminal._background_notices_lock:
            notices = list(self.terminal.background_notices)
            self.terminal.background_notices.clear()
        return notices

    def suggest_synthetic_failure_follow_up(self, *, label: str = "") -> None:
        """Queue RCA prefill after a failed synthetic run and refresh the active prompt."""
        self.terminal.pending_prompt_default = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST
        self.notify_prompt_changed()
        self._bind_last_synthetic_observation(_scenario_id_from_synthetic_label(label))
        self.notify_prompt_changed()

    def record_incoming_alert(self, alert: IncomingAlert) -> None:
        """Append a full IncomingAlert with all metadata to session history.

        Also stores the alert in the ``alerts`` inbox facet (bounded FIFO), preserving
        received_at, severity, source, and alert_name so /status displays accurate
        timestamps and future uses have complete data.
        """
        self.history.append({"type": "incoming_alert", "text": alert.text, "ok": True})
        self.storage.append_turn(self, "incoming_alert", alert.text)
        self.alerts.add(alert)

    def set_turn_outcome_hint(self, hint: str | None) -> None:
        """Attach a structured outcome for the current terminal handler."""
        self.terminal._turn_outcome_hint = (
            hint.strip() if isinstance(hint, str) and hint.strip() else None
        )

    def pop_turn_outcome_hint(self) -> str | None:
        """Return and clear any structured outcome hint for this turn."""
        hint = self.terminal._turn_outcome_hint
        self.terminal._turn_outcome_hint = None
        return hint

    def set_pending_turn_llm(self, run: Any | None) -> None:
        """Stage LLM run metadata for this turn's prompt-recorder flush."""
        self.terminal._pending_turn_llm = run

    def pop_pending_turn_llm(self) -> Any | None:
        """Return and clear staged LLM run metadata for this turn."""
        run = self.terminal._pending_turn_llm
        self.terminal._pending_turn_llm = None
        return run

    def set_pending_turn_error(self, kind: str, message: str) -> None:
        """Stage a structured turn error for this turn's prompt-recorder flush."""
        kind = kind.strip()
        message = message.strip()
        if kind or message:
            self.terminal._pending_turn_error = (kind or "error", message)

    def pop_pending_turn_error(self) -> tuple[str, str] | None:
        """Return and clear the staged structured turn error."""
        error = self.terminal._pending_turn_error
        self.terminal._pending_turn_error = None
        return error

    def clear(self, *, rotate_identity: bool = True) -> None:
        """Reset the session to a fresh state (used by /new and /resume)."""
        self.terminal.history_generation += 1
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
        self.tokens.reset()
        self.agent.clear()
        self.alerts.clear()
        # Keep persisted cross-session task history on disk intact.
        # /new is session-scoped, so swap in a fresh in-memory registry
        # that reuses the same backing store (if any) so /tasks still shows history.
        persist_path = self.terminal.task_registry._persist_path
        self.terminal.task_registry = (
            TaskRegistry(persist_path=persist_path, load=False)
            if persist_path is not None
            else TaskRegistry()
        )

        self.terminal.metrics.reset()
        self.terminal.pending_prompt_default = None
        self.terminal.pending_prompt_autosubmit = False
        self.terminal.exclusive_stdin_active = False
        self.terminal.agent_turn_executed_slashes.clear()
        self.last_synthetic_observation_path = None
        self.terminal.background_mode_enabled = False
        self.terminal.background_investigations.clear()
        # Preserve notification channel prefs across /new like trust_mode.
        # Only reset when the user explicitly changes them via /background notify.
        with self.terminal._background_notices_lock:
            self.terminal.background_notices.clear()
        # trust_mode and reasoning_effort are intentionally preserved across /new
        if rotate_identity:
            # Rotate session identity so the new post-reset session gets its own ID and file.
            self.session_id = str(uuid.uuid4())
            self.started_at = time.time()

    def release_resources(self) -> None:
        """Cancel background work and drop references for terminal teardown.

        Called when this handle is being discarded (see ``SessionManager.close``),
        so the session owns its own teardown instead of exposing internals to
        callers. Uses the same locks as :meth:`clear`, so cancelling the
        in-flight integration-warm task is thread-safe against a background
        warm thread.
        """
        with self._integration_warm_lock:
            self._integration_warm_generation += 1
            pending = self._integration_warm_task
            self._integration_warm_task = None
        if pending is not None and not pending.done():
            pending.cancel()
        with self.terminal._background_notices_lock:
            self.terminal.background_notices.clear()
        self.terminal.prompt_refresh_fn = None
        self.terminal.fleet_sampler_starter = None

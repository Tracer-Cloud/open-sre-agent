"""Interactive-shell session: SessionCore plus terminal UI state.

Extends :class:`~core.agent_harness.session.session_core.SessionCore` with the
shell-only facets (``terminal`` UI/background state and the ``alerts`` inbox) and
the methods that drive them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from config.constants.prompts import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST
from core.agent_harness.session.session_core import SessionCore
from core.domain.alerts.inbox import IncomingAlert
from surfaces.interactive_shell.session.alert_inbox import SessionAlertInbox
from surfaces.interactive_shell.session.terminal_session import TerminalSession

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
    pending-prompt/stdin, background-jobs, and metrics clusters (#3690)."""

    alerts: SessionAlertInbox = field(default_factory=SessionAlertInbox)
    """Inbox of externally-received alerts (shell alert listener → ``/status``).

    A surface facet: the bounded alert list + cap live on ``SessionAlertInbox`` so
    core-session consumers that never touch alerts don't see the field."""

    def suggest_synthetic_failure_follow_up(self, *, label: str = "") -> None:
        """Queue RCA prefill after a failed synthetic run and refresh the active prompt."""
        self.terminal.pending_prompt_default = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST
        self.terminal.notify_prompt_changed()
        self._bind_last_synthetic_observation(_scenario_id_from_synthetic_label(label))
        self.terminal.notify_prompt_changed()

    def _bind_last_synthetic_observation(self, scenario_id: str) -> None:
        """Point ``last_synthetic_observation_path`` (a core field) at the run's latest.json.

        Synthetic-run UX, so it lives on the shell session rather than the core.
        """
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

    def record_incoming_alert(self, alert: IncomingAlert) -> None:
        """Append a full IncomingAlert with all metadata to session history.

        Also stores the alert in the ``alerts`` inbox facet (bounded FIFO), preserving
        received_at, severity, source, and alert_name so /status displays accurate
        timestamps and future uses have complete data.
        """
        self.history.append({"type": "incoming_alert", "text": alert.text, "ok": True})
        self.storage.append_turn(self, "incoming_alert", alert.text)
        self.alerts.add(alert)

    def clear(self, *, rotate_identity: bool = True) -> None:
        """Reset the session — core state plus the shell facets — for /new and /resume."""
        self.terminal.history_generation += 1
        super().clear(rotate_identity=rotate_identity)
        self.alerts.clear()
        self.terminal.metrics.reset()
        self.terminal.pending_prompt_default = None
        self.terminal.pending_prompt_autosubmit = False
        self.terminal.exclusive_stdin_active = False
        self.terminal.agent_turn_executed_slashes.clear()
        self.terminal.background_mode_enabled = False
        self.terminal.background_investigations.clear()
        # Preserve notification channel prefs across /new like trust_mode.
        # Only reset when the user explicitly changes them via /background notify.
        with self.terminal._background_notices_lock:
            self.terminal.background_notices.clear()
        # trust_mode and reasoning_effort are intentionally preserved across /new

    def release_resources(self) -> None:
        """Cancel background work and drop loop-owned UI references for teardown.

        Extends :meth:`SessionCore.release_resources` (which cancels the
        integration-warm task) with the shell facet's own teardown.
        """
        super().release_resources()
        with self.terminal._background_notices_lock:
            self.terminal.background_notices.clear()
        self.terminal.prompt_refresh_fn = None
        self.terminal.fleet_sampler_starter = None

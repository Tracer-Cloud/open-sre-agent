"""Interactive-shell (terminal) session facet.

Groups the shell-surface-only session state (prompt-toolkit, theme, background jobs,
metrics, per-turn analytics staging) that ``core``, ``gateway``, and ``tools``
consumers never touch. Composed onto :class:`~surfaces.interactive_shell.session.session.Session`
as ``session.terminal`` and always present (empty for non-shell sessions), so shell
code accesses fields without a None-guard.

Populated cluster-by-cluster as the #3690 split lands; theme is the first cluster.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
    BackgroundNotificationPreferences,
)
from surfaces.interactive_shell.session.terminal_metrics import TerminalMetrics

if TYPE_CHECKING:
    from prompt_toolkit.history import History


@dataclass
class TerminalSession:
    """Shell-surface session state, composed onto ``Session`` for the interactive shell."""

    active_theme_name: str = "green"
    """Interactive shell palette name for this REPL session (``/theme``, prompts)."""

    pending_theme_refresh: bool = False
    """When True, apply the active palette to prompt-toolkit before the next prompt."""

    trust_mode: bool = False
    """When True, confirmation prompts for elevated REPL actions are skipped."""

    prompt_history_backend: History | None = None
    """The live ``prompt_toolkit.History`` object backing the input prompt.

    Stored here so ``/history`` and ``/privacy`` slash commands can mutate its
    ``paused`` flag (when it is a ``RedactingFileHistory``) without needing access to
    the ``PromptSession``."""

    prompt_app: Any = None
    """The prompt-toolkit ``Application`` instance for this session.

    Stored here (instead of accessed via ``get_app_or_none()``) so that worker-thread
    slash commands (e.g. ``/theme``) can refresh styles via ``call_soon_threadsafe`` on
    the main asyncio loop."""

    main_loop: Any = None
    """The asyncio event loop for the main REPL coroutine.

    Set once by ``InteractiveShellController.start_interactive_shell`` so worker-thread
    code can schedule prompt-toolkit updates on the main thread."""

    prompt_refresh_fn: Callable[[], None] | None = field(default=None, repr=False)
    """Loop-owned hook to apply pending prefill and redraw the active prompt."""

    fleet_sampler_starter: Callable[[], None] | None = field(default=None, repr=False)
    """Loop-owned hook to lazily start the fleet sampler on first live ``/fleet`` use.

    Set by the interactive-shell controller so the sampler (and its ``psutil`` dependency)
    stays out of base REPL startup and only runs when fleet monitoring is actually
    requested. Thread-safe: the starter marshals task creation onto the REPL event loop."""

    pending_prompt_default: str | None = None
    """When set, the next interactive prompt is pre-filled with this string (then cleared)."""

    pending_prompt_autosubmit: bool = False
    """When True alongside ``pending_prompt_default``, the prefilled prompt is
    submitted automatically instead of waiting for the user to press Enter.

    Used to auto-launch an interactive command the agent decided to run (e.g.
    ``/integrations setup sentry``) so it flows through the normal
    exclusive-stdin dispatch path — the only place an interactive child process
    gets clean stdin."""

    exclusive_stdin_active: bool = False
    """True while a turn is running with exclusive stdin reserved (no live prompt).

    Inline picker/wizard slash commands must dispatch immediately during these
    turns instead of re-queueing via ``set_auto_command``, which would loop."""

    agent_turn_executed_slashes: set[str] = field(default_factory=set, repr=False)
    """Slash command lines already executed during the current action-agent turn.

    Prevents the tool-calling loop from re-dispatching the same literal slash
    command when the model emits a duplicate ``slash_invoke`` on a later iteration."""

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

    _background_notices_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    history_generation: int = 0
    """Incremented on /new so background synthetic watchers can skip stale history writes."""

    metrics: TerminalMetrics = field(default_factory=TerminalMetrics)
    """Interactive-shell turn/intervention analytics counters (see ``/status``)."""

    _turn_outcome_hint: str | None = field(default=None, repr=False, compare=False)
    """Optional structured outcome set by a terminal handler for analytics."""

    _pending_turn_llm: Any | None = field(default=None, repr=False, compare=False)
    """LLM run metadata (an ``LlmRunInfo``) staged by a terminal handler for the
    current turn's prompt-recorder flush. Consumed exactly once via
    ``pop_pending_turn_llm`` so it cannot leak into later turns."""

    _pending_turn_error: tuple[str, str] | None = field(default=None, repr=False, compare=False)
    """Structured ``(error_kind, message)`` staged by a failing handler for the
    current turn's prompt-recorder flush. Consumed exactly once via
    ``pop_pending_turn_error`` so it cannot leak into later turns."""

    # ── behavior over the fields above (Session delegates via ``session.terminal``) ──

    def pop_pending_prompt_default(self) -> str:
        """Return pre-filled text for the next prompt line, if any, and clear it."""
        value = self.pending_prompt_default
        self.pending_prompt_default = None
        return value or ""

    def pop_pending_autosubmit(self) -> bool:
        """Return whether the pending prefill should auto-submit, and clear the flag."""
        value = self.pending_prompt_autosubmit
        self.pending_prompt_autosubmit = False
        return value

    def set_auto_command(self, command: str) -> None:
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

    def ensure_fleet_sampler_started(self) -> None:
        """Request that the fleet sampler start (no-op if unwired or already running)."""
        if self.fleet_sampler_starter is not None:
            self.fleet_sampler_starter()

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

    def set_turn_outcome_hint(self, hint: str | None) -> None:
        """Attach a structured outcome for the current terminal handler."""
        self._turn_outcome_hint = hint.strip() if isinstance(hint, str) and hint.strip() else None

    def pop_turn_outcome_hint(self) -> str | None:
        """Return and clear any structured outcome hint for this turn."""
        hint = self._turn_outcome_hint
        self._turn_outcome_hint = None
        return hint

    def set_pending_turn_llm(self, run: Any | None) -> None:
        """Stage LLM run metadata for this turn's prompt-recorder flush."""
        self._pending_turn_llm = run

    def pop_pending_turn_llm(self) -> Any | None:
        """Return and clear staged LLM run metadata for this turn."""
        run = self._pending_turn_llm
        self._pending_turn_llm = None
        return run

    def set_pending_turn_error(self, kind: str, message: str) -> None:
        """Stage a structured turn error for this turn's prompt-recorder flush."""
        kind = kind.strip()
        message = message.strip()
        if kind or message:
            self._pending_turn_error = (kind or "error", message)

    def pop_pending_turn_error(self) -> tuple[str, str] | None:
        """Return and clear the staged structured turn error."""
        error = self._pending_turn_error
        self._pending_turn_error = None
        return error

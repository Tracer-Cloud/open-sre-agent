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

    pt_style_app: Any = None
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
    turns instead of re-queueing via ``queue_auto_command``, which would loop."""

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

    _background_notices_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

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

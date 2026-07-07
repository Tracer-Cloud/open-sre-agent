"""Interactive-shell (terminal) session facet.

Groups the shell-surface-only session state (prompt-toolkit, theme, background jobs,
metrics, per-turn analytics staging) that ``core``, ``gateway``, and ``tools``
consumers never touch. Composed onto :class:`~core.agent_harness.session.state.Session`
as ``session.terminal`` and always present (empty for non-shell sessions), so shell
code accesses fields without a None-guard.

Populated cluster-by-cluster as the #3690 split lands; theme is the first cluster.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prompt_toolkit.history import History


@dataclass
class TerminalSession:
    """Shell-surface session state, composed onto ``Session`` for the interactive shell."""

    active_theme_name: str = "green"
    """Interactive shell palette name for this REPL session (``/theme``, prompts)."""

    pending_theme_refresh: bool = False
    """When True, apply the active palette to prompt-toolkit before the next prompt."""

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

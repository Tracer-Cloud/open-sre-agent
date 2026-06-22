from __future__ import annotations

from app.cli.interactive_shell.ui.output.console_state import (
    set_live_console,
    set_prompt_suppress_fn,
    stop_display,
    unregister_live_console,
)
from app.cli.interactive_shell.ui.output.environment import (
    _repl_progress_active,
    _safe_print,
    debug_print,
    get_output_format,
)
from app.cli.interactive_shell.ui.output.events import ProgressEvent
from app.cli.interactive_shell.ui.output.renderers import (
    render_completed_investigation_footer,
    render_divider,
    render_event,
    render_footer,
    render_investigation_header,
)
from app.cli.interactive_shell.ui.output.toggles import (
    CtrlOToggleWatcher,
    register_tool_detail_toggle,
    suppress_stdin_watchers,
    toggle_active_tool_details,
)
from app.cli.interactive_shell.ui.output.tracker import (
    ProgressTracker,
    get_tracker,
    reset_tracker,
    set_silent_tracker,
)
from app.cli.interactive_shell.ui.time_format import _fmt_timing

__all__ = [
    # Tracker / progress
    "ProgressEvent",
    "ProgressTracker",
    "get_tracker",
    "reset_tracker",
    "set_silent_tracker",
    # Rendering
    "render_completed_investigation_footer",
    "render_divider",
    "render_event",
    "render_footer",
    "render_investigation_header",
    # Console lifecycle
    "set_live_console",
    "set_prompt_suppress_fn",
    "stop_display",
    "unregister_live_console",
    # Tool-detail toggle
    "CtrlOToggleWatcher",
    "register_tool_detail_toggle",
    "suppress_stdin_watchers",
    "toggle_active_tool_details",
    # Output config
    "debug_print",
    "get_output_format",
    # Semi-public helpers used by app/cli/ui/renderer (underscore names are
    # intentional — they signal "reach in carefully" rather than stable API)
    "_fmt_timing",
    "_repl_progress_active",
    "_safe_print",
]

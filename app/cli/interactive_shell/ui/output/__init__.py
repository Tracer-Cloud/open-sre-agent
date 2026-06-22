from __future__ import annotations

from app.cli.interactive_shell.ui.output.console_state import (
    _get_console,
    set_live_console,
    set_prompt_suppress_fn,
    stop_display,
    unregister_live_console,
)
from app.cli.interactive_shell.ui.output.environment import (
    _is_silent_output,
    _is_verbose,
    _repl_progress_active,
    _safe_print,
    debug_print,
    get_output_format,
)
from app.cli.interactive_shell.ui.output.events import ProgressEvent
from app.cli.interactive_shell.ui.output.labels import (
    _humanise_message,
    _node_event_type,
    _node_label,
    _node_phase_label,
)
from app.cli.interactive_shell.ui.output.live_display import _EventLogDisplay
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
from app.cli.interactive_shell.ui.time_format import _elapsed_hms, _fmt_timing

__all__ = [
    "CtrlOToggleWatcher",
    "ProgressEvent",
    "ProgressTracker",
    "_EventLogDisplay",
    "_elapsed_hms",
    "_fmt_timing",
    "_get_console",
    "_humanise_message",
    "_is_silent_output",
    "_is_verbose",
    "_node_event_type",
    "_node_label",
    "_node_phase_label",
    "_repl_progress_active",
    "_safe_print",
    "debug_print",
    "get_output_format",
    "get_tracker",
    "register_tool_detail_toggle",
    "render_completed_investigation_footer",
    "render_divider",
    "render_event",
    "render_footer",
    "render_investigation_header",
    "reset_tracker",
    "set_live_console",
    "set_prompt_suppress_fn",
    "set_silent_tracker",
    "stop_display",
    "suppress_stdin_watchers",
    "toggle_active_tool_details",
    "unregister_live_console",
]

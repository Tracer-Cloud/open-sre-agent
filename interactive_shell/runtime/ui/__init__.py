"""REPL terminal UI adapters (streaming console, CPR stdin, progress, notifications)."""

from interactive_shell.runtime.ui.background_notifications import deliver_background_notifications
from interactive_shell.runtime.ui.cpr_stdin import (
    contains_cpr_sequence,
    drain_stale_cpr_bytes,
    strip_cpr_escape_sequences,
    strip_cpr_sequences,
)
from interactive_shell.runtime.ui.foreground_investigation import run_foreground_investigation
from interactive_shell.runtime.ui.repl_progress import (
    repl_safe_progress_requested,
    repl_safe_progress_scope,
)
from interactive_shell.runtime.ui.streaming_console import StreamingConsole

__all__ = [
    "StreamingConsole",
    "contains_cpr_sequence",
    "deliver_background_notifications",
    "drain_stale_cpr_bytes",
    "repl_safe_progress_requested",
    "repl_safe_progress_scope",
    "run_foreground_investigation",
    "strip_cpr_escape_sequences",
    "strip_cpr_sequences",
]

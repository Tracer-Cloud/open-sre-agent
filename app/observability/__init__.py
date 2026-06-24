"""Observability ports — abstractions core code uses to report progress,
debug output, and check the runtime output format.

Core agent/pipeline/utils code depends only on these ports. Concrete
implementations live in adapter packages (e.g. the Rich-backed REPL
tracker under ``app/cli/interactive_shell/ui/output/``). The boundary
(typically the CLI entry point) registers the adapter via the
``set_progress_tracker`` / ``set_debug_printer`` injection helpers.

This is the same Ports & Adapters pattern used by
``app/agent/correlation/`` (see :func:`build_upstream_evidence_provider`):
high-level modules depend on abstractions, not concretions.
"""

from __future__ import annotations

from app.observability.debug import debug_print, set_debug_printer
from app.observability.display import (
    render_completed_investigation_footer,
    render_investigation_header,
    set_investigation_footer_renderer,
    set_investigation_header_renderer,
)
from app.observability.output_format import get_output_format
from app.observability.progress import (
    NoopProgressTracker,
    ProgressTracker,
    get_progress_tracker,
    set_progress_tracker,
    set_progress_tracker_factory,
    silence_progress_tracker,
)

__all__ = [
    "NoopProgressTracker",
    "ProgressTracker",
    "debug_print",
    "get_output_format",
    "get_progress_tracker",
    "render_completed_investigation_footer",
    "render_investigation_header",
    "set_debug_printer",
    "set_investigation_footer_renderer",
    "set_investigation_header_renderer",
    "set_progress_tracker",
    "set_progress_tracker_factory",
    "silence_progress_tracker",
]

from __future__ import annotations

import contextlib
import os
import sys

from app.cli.interactive_shell.runtime.repl_progress import repl_safe_progress_requested
from app.cli.interactive_shell.ui.theme import SECONDARY
from app.observability.output_format import get_output_format


def _is_silent_output() -> bool:
    return get_output_format() == "none"


def _repl_progress_active() -> bool:
    """True when investigation progress must not use Rich Live."""
    if repl_safe_progress_requested():
        return True
    try:
        from prompt_toolkit.application.current import get_app_or_none
    except ImportError:  # pragma: no cover - optional in minimal installs
        return False
    return get_app_or_none() is not None


def _safe_print(text: str) -> None:
    """Print text, replacing unencodable characters."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        with contextlib.suppress(BrokenPipeError):
            print(text.encode(enc, errors="replace").decode(enc))
    except BrokenPipeError:
        pass


def _is_verbose() -> bool:
    if os.getenv("TRACER_VERBOSE", "").lower() in ("1", "true", "yes"):
        return True
    try:
        from app.cli.interactive_shell.data_store.context import is_debug, is_verbose

        return is_verbose() or is_debug()
    except Exception:
        return False


def debug_print(message: str) -> None:
    if not _is_verbose():
        return
    if get_output_format() == "rich":
        from app.cli.interactive_shell.ui.output.console_state import _get_console

        _get_console().print(f"[{SECONDARY}]{message}[/]")
    else:
        print(f"DEBUG: {message}")


def install_cli_observability_adapters() -> None:
    """Wire CLI implementations into the observability ports.

    Call once from the CLI boundary (typically the REPL/CLI start-up).
    Idempotent — re-registers the same callables so calling it twice
    is a no-op.

    Wires:
    - debug_print: stderr default → Rich-aware CLI version
    - render_investigation_header: no-op default → Rich panel
    - progress tracker: Noop default → Rich-backed CLI singleton
    """
    from app.cli.interactive_shell.ui.output.renderers import (
        render_completed_investigation_footer,
        render_investigation_header,
    )
    from app.cli.interactive_shell.ui.output.tracker import get_tracker
    from app.observability.debug import set_debug_printer
    from app.observability.display import (
        set_investigation_footer_renderer,
        set_investigation_header_renderer,
    )
    from app.observability.progress import set_progress_tracker_factory

    set_debug_printer(debug_print)
    set_investigation_header_renderer(render_investigation_header)
    set_investigation_footer_renderer(render_completed_investigation_footer)
    # Lazy: first core ``get_progress_tracker()`` call constructs the CLI
    # tracker after REPL boot so ``_repl_progress_active()`` is accurate.
    set_progress_tracker_factory(get_tracker)

from __future__ import annotations

import contextlib
import os
import sys

from cli.interactive_shell.runtime.repl_progress import repl_safe_progress_requested
from cli.interactive_shell.ui.theme import SECONDARY
from platform.observability.output_format import get_output_format


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
        from cli.interactive_shell.data_store.context import is_debug, is_verbose

        return is_verbose() or is_debug()
    except Exception:
        return False


def debug_print(message: str) -> None:
    if not _is_verbose():
        return
    if get_output_format() == "rich":
        from cli.interactive_shell.ui.output.console_state import _get_console

        _get_console().print(f"[{SECONDARY}]{message}[/]")
    else:
        print(f"DEBUG: {message}")


# ``install_product_adapters`` lives in
# :mod:`cli.interactive_shell.ui.output.boundary`, not here. Putting
# it in this module would re-introduce a static import cycle:
# ``renderers`` and ``tracker`` already import from this module for
# utility plumbing, and the install function imports them back. Moving
# the wiring into a leaf module keeps the static graph acyclic.

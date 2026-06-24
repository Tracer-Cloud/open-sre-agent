from __future__ import annotations

import contextlib
import os
import sys

from app.cli.interactive_shell.runtime.repl_progress import repl_safe_progress_requested
from app.cli.interactive_shell.ui.theme import SECONDARY


def get_output_format() -> str:
    """Return 'rich' for interactive TTY, 'text' otherwise."""
    if fmt := os.getenv("TRACER_OUTPUT_FORMAT"):
        return fmt
    if os.getenv("NO_COLOR") is not None:
        return "text"
    if os.getenv("SLACK_WEBHOOK_URL"):
        return "text"
    return "rich" if sys.stdout.isatty() else "text"


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

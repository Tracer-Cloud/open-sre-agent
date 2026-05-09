"""Textual-based REPL for Claude-Code-style UX (#1679).

textual is a TUI framework with a React-like component model: declarative
widgets, automatic re-render on state changes, proper layout management.
This is the Python equivalent of Ink (which Claude Code uses) and lets us
build the "input pinned at bottom + output flows above + streaming
indicator" pattern that ``prompt_toolkit`` couldn't compose cleanly.
"""

from app.cli.interactive_shell.textual_repl.app import (
    OpenSREApp,
    run_textual_repl,
)
from app.cli.interactive_shell.textual_repl.console_adapter import TextualConsole

__all__ = ["OpenSREApp", "TextualConsole", "run_textual_repl"]

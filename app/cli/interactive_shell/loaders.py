"""Shared Rich loaders for interactive-shell LLM calls.

A bright orange spinner gives the user visible feedback that an LLM call is
in flight.  Centralised so every LLM-backed surface in the interactive
shell (``cli_agent``, ``cli_help``, ``follow_up``) shares the same look.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console

# 256-colour orange that reads well on both light and dark terminal
# themes; Rich auto-degrades to ANSI on 8-colour terminals.
_LOADER_COLOR = "orange1"
# ``dots12`` rotates a denser braille pattern than the default ``dots`` —
# smoother motion for short waits without taking extra horizontal space.
_LOADER_SPINNER = "dots12"

DEFAULT_LOADER_LABEL = "thinking"


@contextmanager
def llm_loader(console: Console, label: str = DEFAULT_LOADER_LABEL) -> Iterator[None]:
    """Render an orange spinner while an LLM call is in flight.

    On non-terminal consoles (CI, captured output, piped stdout) the
    spinner is skipped so captured logs stay clean — the wrapped call
    still runs unchanged.
    """
    if not console.is_terminal:
        yield
        return

    style = f"bold {_LOADER_COLOR}"
    text = f"[{style}]{label}…[/{style}]"
    with console.status(text, spinner=_LOADER_SPINNER, spinner_style=style):
        yield


__all__ = ["DEFAULT_LOADER_LABEL", "llm_loader"]

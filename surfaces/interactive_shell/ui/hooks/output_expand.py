"""Ctrl+O pages the last collapsed tool-output peek (Droid-style).

A long tool result renders a short head plus ``… N more, Ctrl+O to view``.
This hook opens the full text in the user's pager, then returns to the
session. The binding is gated on a collapsed body being stashed, so Ctrl+O
falls through when nothing was folded.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent


def page_collapsed_output(text: str) -> None:
    """Show *text* in ``$PAGER`` / ``less``, or print it when no pager exists."""
    spec = os.environ.get("PAGER") or shutil.which("less") or ""
    cmd = shlex.split(spec) if spec else []
    if cmd:
        subprocess.run(cmd, input=text.encode(), check=False)
        return
    sys.stdout.write(text if text.endswith("\n") else f"{text}\n")
    sys.stdout.flush()


def install_output_expand_key_bindings(
    has_output: Callable[[], bool],
    get_output: Callable[[], str],
    page: Callable[[str], None],
) -> KeyBindings:
    """Bind Ctrl+O to page the stashed collapsed tool result."""
    kb = KeyBindings()
    output_shown = Condition(has_output)

    @kb.add("c-o", filter=output_shown, eager=True)
    def _page_output(_event: KeyPressEvent) -> None:
        body = get_output()
        if body:
            page(body)

    return kb


__all__ = ["install_output_expand_key_bindings", "page_collapsed_output"]

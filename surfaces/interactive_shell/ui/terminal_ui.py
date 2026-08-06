"""Single entry point composing the full terminal UI render.

The terminal UI has four pieces, all composed from this module:

1. splash screen (Braille logo, version, first-run notice)
2. welcome panel (identity column + tips/ambient status)
3. hint/spinner line above the prompt rule
4. prompt rule + ``[n] ❯`` input line

Pieces 1–2 are static chrome printed once by :func:`render_terminal_ui`.
Pieces 3–4 form the live prompt region: prompt-toolkit re-evaluates them on
every keystroke, spinner tick, and prompt invalidation, so they are composed
by :func:`render_prompt_region`, which ``PromptManager`` calls per redraw.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import ANSI
from rich.console import Console

from surfaces.interactive_shell.ui.banner import render_ready_box, render_splash
from surfaces.interactive_shell.ui.components.cpr_stdin import strip_cpr_sequences
from surfaces.interactive_shell.ui.input_prompt import rendering as prompt_rendering

if TYPE_CHECKING:
    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.session import Session


def render_terminal_ui(
    console: Console | None = None,
    *,
    session: object = None,
    first_run: bool | None = None,
) -> None:
    """Render the static terminal chrome: splash screen then welcome panel."""
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_splash(console, first_run=first_run)
    render_ready_box(console, session=session)


def render_prompt_region(session: Session, state: ReplState, spinner: SpinnerState) -> ANSI:
    """Compose the live prompt region: context line plus rule and input prefix.

    The top line is the pending confirmation prompt when one is active,
    otherwise the spinner, completion preview, or idle hint.
    """
    base = prompt_rendering._prompt_message(session).value
    if state.is_awaiting_confirmation():
        return ANSI(f"{state.confirm_prompt_text}\n{base}")
    prefix = strip_cpr_sequences(
        prompt_rendering.resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=prompt_rendering.resolve_idle_hint_ansi(session),
        )
    )
    return ANSI(f"{prefix}\n{base}")


__all__ = ["render_prompt_region", "render_terminal_ui"]

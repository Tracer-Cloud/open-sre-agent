"""Single entry point composing the full terminal UI render.

The terminal UI has three pieces, all composed from this module:

1. compact launch banner (overlapping-ring mark + capability status)
2. hint/spinner and autonomy status above the composer
3. bordered ``>`` composer + help footer

Piece 1 is static chrome printed once by :func:`render_terminal_ui`.
Pieces 2–3 form the live prompt region: prompt-toolkit re-evaluates them on
every keystroke, spinner tick, and prompt invalidation, so they are composed
by :func:`render_prompt_region`, which ``PromptBuilder`` calls per redraw.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import ANSI
from rich.console import Console

from surfaces.interactive_shell.ui.auto_status import auto_status_ansi
from surfaces.interactive_shell.ui.input_prompt import rendering as prompt_rendering
from surfaces.interactive_shell.ui.prompt_visibility import (
    hidden_typing_box_pad,
    typing_box_hidden,
)
from surfaces.interactive_shell.ui.task_plan import task_plan_overlay_ansi
from surfaces.shared.terminal.banner import render_launch_banner
from surfaces.shared.terminal.components.cpr_stdin import strip_cpr_sequences
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_line_width

if TYPE_CHECKING:
    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.session import Session


def render_terminal_ui(
    console: Console | None = None,
    *,
    session: object = None,
) -> None:
    """Render the static terminal chrome: the compact launch banner."""
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_launch_banner(console, session=session)


def render_prompt_region(session: Session, state: ReplState, spinner: SpinnerState) -> ANSI:
    """Compose the live prompt region: context line plus rule and input prefix.

    The top line is the pending confirmation prompt when one is active,
    otherwise the spinner, completion preview, or idle hint, followed by the
    autonomy status line showing the active ``/auto`` level.

    When confirmation or exclusive-stdin structured input owns the keyboard,
    the free-text typing box (rule + ``[N] ❯``) is omitted so it does not
    compete with Ask User / option menus — free text is itself an option
    (``Or type your own answer...``), not a parallel composer.

    The region always starts with one blank row so the hint/spinner line never
    sits flush against whatever output scrolled above it. The row is constant
    across all prompt states (no height delta between redraws) and is erased
    with the rest of the region on submit (``erase_when_done=True``).
    """
    if typing_box_hidden(session, state):
        # Same newline count as ``_prompt_message`` so confirmation does not
        # shift the live region height under ``patch_stdout``.
        base = hidden_typing_box_pad()
    else:
        base = prompt_rendering._prompt_message(session).value
    auto_line = strip_cpr_sequences(auto_status_ansi(session))
    # The confirmation prompt takes the prefix row so every state renders the
    # same rows (blank, prefix, auto status, input) — no height delta on redraw.
    if state.is_awaiting_confirmation():
        # Same one-row budget as the spinner/idle spacer: a long confirm string
        # must not soft-wrap when the spinner path is clipped to width.
        prefix = clip_prompt_text(state.confirm_prompt_text, prompt_line_width())
    elif state.is_ctrl_c_exit_hint_visible():
        prefix = prompt_rendering.ctrl_c_exit_hint_ansi()
    else:
        prefix = strip_cpr_sequences(
            prompt_rendering.resolve_prompt_prefix_ansi(
                inline_spinner=spinner.inline_spinner_ansi(),
                idle_hint=prompt_rendering.resolve_idle_hint_ansi(session),
            )
        )
    # A live plan sits as an overlay above the prefix. Its presence tracks
    # ``session.task_plan`` (stable across redraws), not the prompt state, so it
    # adds a constant row whether or not a confirmation is pending.
    plan = session.task_plan
    if plan is not None and plan.steps:
        overlay = strip_cpr_sequences(task_plan_overlay_ansi(plan))
        # A blank row separates the plan block from the status line so the
        # checklist reads as its own element, not flush against the prompt.
        return ANSI(f"\n{overlay}\n\n{prefix}\n{auto_line}\n{base}")
    return ANSI(f"\n{prefix}\n{auto_line}\n{base}")


__all__ = ["render_prompt_region", "render_terminal_ui"]

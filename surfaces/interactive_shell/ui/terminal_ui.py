"""Single entry point composing the full terminal UI render.

The terminal UI has three pieces, all composed from this module:

1. compact launch banner (wordmark + install health)
2. one status row above the composer — Auto while idle, Thinking / Invoking
   while busy
3. bordered ``>`` composer (job-shaped placeholder; no help footer)

Piece 1 is static chrome printed once by :func:`render_terminal_ui`.
Pieces 2–3 form the live prompt region: prompt-toolkit re-evaluates them on
every keystroke, spinner tick, and prompt invalidation, so they are composed
by :func:`render_prompt_region`, which ``PromptBuilder`` calls per redraw.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import ANSI
from rich.console import Console

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.auto_status import auto_status_ansi
from surfaces.interactive_shell.ui.hooks import confirmation_choice_overlay_ansi
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
    otherwise one status row: Thinking / Invoking while a turn is running, or
    the ``/auto`` permission line while idle. A rendered task plan has one
    blank row beneath it before this status chrome.

    When confirmation or exclusive-stdin structured input owns the keyboard,
    the free-text typing box (rule + ``[N] ❯``) is omitted so it does not
    compete with Ask User / option menus — free text is itself an option
    (``Or type your own answer...``), not a parallel composer.

    One leading blank row separates scrollback from the status slot →
    composer, matching Droid's row margin between transcript and chrome. Idle
    still has no empty "Ready" placeholder under the banner.
    """
    if typing_box_hidden(session, state):
        # Same newline count as ``_prompt_message`` so confirmation does not
        # shift the live region height under ``patch_stdout``.
        base = hidden_typing_box_pad()
    else:
        base = prompt_rendering._prompt_message(session).value
    plan = session.task_plan
    if plan is None or not plan.steps:
        # Drop expand so the next plan opens collapsed rather than inheriting
        # a sticky Ctrl+P from a previous checklist.
        state.plan_expanded = False
        state.plan_step_texts = None
        plan_overlay = ""
    else:
        # Status-only updates keep expand; a different checklist must not.
        step_texts = tuple(item.step for item in plan.steps)
        if state.plan_step_texts is not None and state.plan_step_texts != step_texts:
            state.plan_expanded = False
        state.plan_step_texts = step_texts
        plan_overlay = strip_cpr_sequences(
            task_plan_overlay_ansi(plan, expanded=state.plan_expanded)
        )
    # Keep one empty row between the pinned plan and the live status chrome.
    plan_prefix = f"{plan_overlay}\n\n" if plan_overlay else ""

    # A pending confirmation renders a stacked, arrow-navigable Yes/No choice
    # (box hidden). Density matches the streaming stack: status → Auto → composer.
    if state.is_awaiting_confirmation():
        auto_line = strip_cpr_sequences(auto_status_ansi(session))
        choice = _confirmation_block(state)
        return ANSI(f"\n{plan_prefix}{choice}\n{auto_line}\n{base}")

    if state.is_ctrl_c_exit_hint_visible():
        prefix = prompt_rendering.ctrl_c_exit_hint_ansi()
        inline_spinner = ""
    else:
        inline_spinner = spinner.inline_spinner_ansi()
        prefix = strip_cpr_sequences(
            prompt_rendering.resolve_prompt_prefix_ansi(
                inline_spinner=inline_spinner,
                idle_hint=prompt_rendering.resolve_idle_hint_ansi(session),
            )
        )
    # Tools already paint a ``⏺`` line into scrollback; the live tool name is
    # folded into the spinner status row (same line as ``Invoking tools…``).
    # Leading blank = Droid row margin under the last transcript line.
    # Busy occupies the same slot as Auto so patch_stdout height stays stable
    # and the bar is one headline, not Thinking plus a leftover Auto row.
    if prefix:
        return ANSI(f"\n{plan_prefix}{prefix}\n{base}")
    auto_line = strip_cpr_sequences(auto_status_ansi(session))
    return ANSI(f"\n{plan_prefix}{auto_line}\n{base}")


_CONFIRM_HINT = "↑↓ Navigate • Enter confirm • Esc cancel"


def _confirmation_block(state: ReplState) -> str:
    """Header, the stacked ``[a] Yes`` / ``[b] No`` choice, and a nav hint.

    Dense like the streaming stack: header, choice, and hint on consecutive
    lines so confirm doesn't feel taller than Thinking/Invoking.
    """
    header = clip_prompt_text(state.confirm_prompt_text.strip(), prompt_line_width())
    header_ansi = f"{ui_theme.SECONDARY_ANSI}{header}{ui_theme.ANSI_RESET}"
    choice = strip_cpr_sequences(
        confirmation_choice_overlay_ansi(state.confirm_selected, state.confirm_options)
    )
    hint = f"{ui_theme.DIM_ANSI}{_CONFIRM_HINT}{ui_theme.ANSI_RESET}"
    return f"{header_ansi}\n{choice}\n{hint}"


__all__ = ["render_prompt_region", "render_terminal_ui"]

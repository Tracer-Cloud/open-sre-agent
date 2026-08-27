"""Batched Ask User wizard for the interactive shell.

One payload with several questions: header **Ask User**, breadcrumb
``● Shape → ○ Onset → ○ Signals`` (filled = answered, open = remaining),
Tab / Shift+Tab between questions, ↑↓ through options, Enter or Submit to
select. Esc cancels (the agent does not continue). ``Or type your own...``
skips auto-submit so the user can type.
"""

from __future__ import annotations

import os
import sys

from core.agent_harness.spi.handoff import AskUserQuestion
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.components.choice_menu import (
    erase_menu_lines,
    menu_columns,
    repl_tty_interactive,
    write_menu_line,
)
from surfaces.shared.terminal.components.key_reader import (
    flush_pending_input,
    read_key_unix,
    read_key_windows,
    restore_stdin_terminal,
)

CUSTOM_OPTION = "Or type your own..."
_HEADER = "Ask User"
_SUBMIT = "Submit"
_HINT = "Tab/⇧Tab or ←/→ Questions    ↑/↓ Navigate    Enter/1-9 Select    Esc cancel"
_BREADCRUMB_SEP = " → "
_MENU_LEADING_LINES = 1
_FILLED = "●"
_OPEN = "○"


def format_ask_user_breadcrumb(
    questions: tuple[AskUserQuestion, ...] | list[AskUserQuestion],
    *,
    answered: tuple[bool, ...] | list[bool],
) -> str:
    """Breadcrumb: ● replied, ○ not yet (current is distinguished by colour, not glyph)."""
    return _BREADCRUMB_SEP.join(
        f"{glyph} {label}" for glyph, label in _breadcrumb_items(questions, answered)
    )


def _breadcrumb_items(
    questions: tuple[AskUserQuestion, ...] | list[AskUserQuestion],
    answered: tuple[bool, ...] | list[bool],
) -> list[tuple[str, str]]:
    """``(glyph, label)`` pairs shared by plain and ANSI breadcrumbs."""
    items: list[tuple[str, str]] = []
    for index, question in enumerate(questions):
        replied = bool(answered[index]) if index < len(answered) else False
        glyph = _FILLED if replied else _OPEN
        label = strip_terminal_controls(question.label).strip() or f"Q{index + 1}"
        items.append((glyph, label))
    return items


def _option_labels(question: AskUserQuestion) -> list[str]:
    return [strip_terminal_controls(option) for option in question.options] + [CUSTOM_OPTION]


def _menu_height(question: AskUserQuestion) -> int:
    # leading, header, breadcrumb, rule, question, choices, Submit, blank, hint
    return _MENU_LEADING_LINES + 1 + 1 + 1 + 1 + len(_option_labels(question)) + 1 + 1 + 1


def _row_count(question: AskUserQuestion) -> int:
    return len(_option_labels(question)) + 1


def _breadcrumb_ansi(
    questions: tuple[AskUserQuestion, ...],
    *,
    current: int,
    answered: tuple[bool, ...],
) -> str:
    """Glyph marks reply state (● replied, ○ not); colour marks position.

    Current step in the accent colour, replied steps in brand (same green as
    answers in the transcript), remaining steps dimmed.
    """
    parts: list[str] = []
    for index, (glyph, label) in enumerate(_breadcrumb_items(questions, answered)):
        if index:
            parts.append(f"{ui_theme.DIM_COUNTER_ANSI}{_BREADCRUMB_SEP}{ui_theme.ANSI_RESET}")
        if index == current:
            style = ui_theme.PROMPT_ACCENT_ANSI
        elif answered[index] if index < len(answered) else False:
            style = ui_theme.BRAND_ANSI
        else:
            style = ui_theme.DIM_COUNTER_ANSI
        parts.append(f"{style}{glyph} {label}{ui_theme.ANSI_RESET}")
    return "".join(parts)


def _padded_row(marker: str, label: str, width: int) -> str:
    content = f" {marker} {label}"
    padding = width - len(content)
    return content + (" " * padding if padding > 0 else "")


def _draw_ask_user(
    *,
    questions: tuple[AskUserQuestion, ...],
    current: int,
    answers: list[str | None],
    option_index: int,
    erase_lines: int,
) -> None:
    question = questions[current]
    labels = _option_labels(question)
    answered = tuple(item is not None for item in answers)
    breadcrumb = _breadcrumb_ansi(questions, current=current, answered=answered)
    width = menu_columns()
    if erase_lines:
        erase_menu_lines(erase_lines)
    for _ in range(_MENU_LEADING_LINES):
        write_menu_line()
    write_menu_line(f"{ui_theme.PROMPT_ACCENT_ANSI}{_HEADER}{ui_theme.ANSI_RESET}")
    write_menu_line(breadcrumb)
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{'─' * width}{ui_theme.ANSI_RESET}")
    write_menu_line(
        f"{ui_theme.TEXT_ANSI}{strip_terminal_controls(question.title)}{ui_theme.ANSI_RESET}"
    )
    submit_row = len(labels)
    for position, label in enumerate(labels):
        is_selected = position == option_index
        numbered_label = f"{position + 1}. {label}"
        marker = ">" if is_selected else " "
        row = _padded_row(marker, numbered_label, width)
        if is_selected:
            write_menu_line(f"{ui_theme.MENU_SELECTION_ROW_ANSI}{row}{ui_theme.ANSI_RESET}")
        else:
            write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{row}{ui_theme.ANSI_RESET}")
    submit_selected = option_index == submit_row
    submit_line = _padded_row(">" if submit_selected else " ", _SUBMIT, width)
    if submit_selected:
        write_menu_line(f"{ui_theme.MENU_SELECTION_ROW_ANSI}{submit_line}{ui_theme.ANSI_RESET}")
    else:
        write_menu_line(f"{ui_theme.PROMPT_ACCENT_ANSI}{submit_line}{ui_theme.ANSI_RESET}")
    write_menu_line()
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{_HINT}{ui_theme.ANSI_RESET}")
    sys.stdout.flush()


def _erase_ask_user(question: AskUserQuestion) -> None:
    erase_menu_lines(_menu_height(question))
    sys.stdout.flush()


def _read_wizard_action() -> str:
    # Space must not confirm — leftover whitespace from the previous prompt
    # would otherwise pick option 1 on every question before the user sees it.
    if os.name == "nt":
        return read_key_windows(space_confirms=False)
    return read_key_unix(space_confirms=False)


def _next_unanswered(answers: list[str | None], start: int) -> int:
    n = len(answers)
    for offset in range(n):
        index = (start + offset) % n
        if answers[index] is None:
            return index
    return start


def repl_ask_user(
    questions: tuple[AskUserQuestion, ...] | list[AskUserQuestion],
) -> tuple[str, ...] | None:
    """Show the Ask User wizard; return selected labels or None on Esc.

    Only call when :func:`repl_tty_interactive` is True. A ``CUSTOM_OPTION``
    value means the user chose to type that answer themselves.
    """
    from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes

    items = tuple(questions)
    if len(items) < 2 or not repl_tty_interactive():
        return None
    drain_stale_cpr_bytes()
    flush_pending_input()
    answers: list[str | None] = [None] * len(items)
    q_idx = 0
    opt_idx = 0
    option_focus = 0
    first = True
    current_height = 0
    while True:
        question = items[q_idx]
        labels = _option_labels(question)
        rows = _row_count(question)
        opt_idx %= rows
        _draw_ask_user(
            questions=items,
            current=q_idx,
            answers=answers,
            option_index=opt_idx,
            erase_lines=0 if first else current_height,
        )
        if first:
            # Drop the newline that submitted this turn / autosubmitted
            # ``/choose``. Flush after the first draw so the menu is on
            # screen before we wait — leftover Enter must not auto-pick.
            flush_pending_input()
            first = False
        current_height = _menu_height(question)
        action = _read_wizard_action()
        if action in ("tab", "right"):
            q_idx = min(q_idx + 1, len(items) - 1)
            opt_idx = 0
            option_focus = 0
            continue
        if action in ("shift_tab", "left"):
            q_idx = max(q_idx - 1, 0)
            opt_idx = 0
            option_focus = 0
            continue
        if action == "up":
            opt_idx = (opt_idx - 1) % rows
            if opt_idx < len(labels):
                option_focus = opt_idx
            continue
        if action == "down":
            opt_idx = (opt_idx + 1) % rows
            if opt_idx < len(labels):
                option_focus = opt_idx
            continue
        selected: int | None = None
        submit_row = len(labels)
        if action == "enter":
            selected = option_focus if opt_idx == submit_row else opt_idx
            if selected < len(labels):
                option_focus = selected
        elif len(action) == 1 and action.isdigit():
            picked = int(action) - 1
            if 0 <= picked < len(labels):
                selected = picked
        if selected is not None:
            answers[q_idx] = labels[selected]
            if all(item is not None for item in answers):
                _erase_ask_user(question)
                restore_stdin_terminal()
                return tuple(str(item) for item in answers)
            q_idx = _next_unanswered(answers, q_idx + 1)
            opt_idx = 0
            option_focus = 0
            continue
        if action in ("cancel", "eof"):
            _erase_ask_user(question)
            restore_stdin_terminal()
            return None
        # ignore / unmapped


__all__ = [
    "CUSTOM_OPTION",
    "format_ask_user_breadcrumb",
    "repl_ask_user",
]

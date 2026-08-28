"""Batched Ask User wizard for the interactive shell.

One payload with several questions: header **Ask User**, breadcrumb
``● Shape → ○ Onset → ○ Signals`` (filled = answered, open = remaining),
Tab / Shift+Tab between questions, ↑↓ through options, Enter or Submit to
select. Esc cancels (the agent does not continue).

Free text is the last row of the same OpenSRE option array (Droid-style):
when that row is focused you type on it in place — the panel never leaves
for a separate ``Your answer:`` or ``[N] ❯`` prompt.

Questions with ``multi_select`` show ``[ ]`` / ``[x]`` checkboxes; Space,
Enter, and digits toggle. Submit commits the checked set.
"""

from __future__ import annotations

import sys

from core.agent_harness.spi.handoff import AskUserQuestion
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.prompt_visibility import clear_live_prompt_paint
from surfaces.shared.terminal.components.choice_menu import (
    erase_menu_lines,
    hide_terminal_cursor,
    leave_inline_menu,
    menu_columns,
    repl_tty_interactive,
    write_menu_line,
)
from surfaces.shared.terminal.components.key_reader import (
    flush_pending_input,
    read_menu_or_char,
)

# Last row of the option array; when focused, typing replaces this label in place.
CUSTOM_OPTION = "Or type your own answer..."
_HEADER = "Ask User"
_SUBMIT = "Submit"
_HINT = "Tab/⇧Tab or ←/→ Questions    ↑/↓ Navigate    Enter/1-9 Select    Esc cancel"
_HINT_MULTI = "Tab/⇧Tab or ←/→ Questions    ↑/↓ Navigate    Space/Enter/1-9 Toggle    Esc cancel"
_HINT_TYPING = "Type on this row    Enter confirm    ↑/↓ leave    Esc cancel"
_CURSOR = "█"
_BREADCRUMB_SEP = " → "
_FILLED = "●"
_OPEN = "○"
_CHECKED = "[x]"
_UNCHECKED = "[ ]"


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
    # header, breadcrumb, rule, question, choices, Submit, hint (tight, no blank gaps)
    return 1 + 1 + 1 + 1 + len(_option_labels(question)) + 1 + 1


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


def _write_option_row(*, prefix: str, label: str, width: int, selected: bool) -> None:
    """Paint one option; accent only the content so the row is not a full-width bar."""
    content = f" {prefix} {label}"
    pad = max(0, width - len(content))
    style = ui_theme.PROMPT_ACCENT_ANSI if selected else ui_theme.DIM_COUNTER_ANSI
    write_menu_line(f"{style}{content}{ui_theme.ANSI_RESET}{' ' * pad}")


def _draw_ask_user(
    *,
    questions: tuple[AskUserQuestion, ...],
    current: int,
    answers: list[str | None],
    option_index: int,
    erase_lines: int,
    custom_draft: str | None = None,
    checked: set[int] | None = None,
) -> None:
    """Paint the OpenSRE option array; ``custom_draft`` edits the last row in place."""
    question = questions[current]
    labels = _option_labels(question)
    answered = tuple(item is not None for item in answers)
    breadcrumb = _breadcrumb_ansi(questions, current=current, answered=answered)
    width = menu_columns()
    multi = question.multi_select
    checked = checked or set()
    if erase_lines:
        erase_menu_lines(erase_lines)
    write_menu_line(f"{ui_theme.PROMPT_ACCENT_ANSI}{_HEADER}{ui_theme.ANSI_RESET}")
    write_menu_line(breadcrumb)
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{'─' * width}{ui_theme.ANSI_RESET}")
    write_menu_line(
        f"{ui_theme.TEXT_ANSI}{strip_terminal_controls(question.title)}{ui_theme.ANSI_RESET}"
    )
    submit_row = len(labels)
    typing = custom_draft is not None
    for position, label in enumerate(labels):
        is_selected = position == option_index
        if label == CUSTOM_OPTION and typing:
            body = f"{custom_draft}{_CURSOR}"
        else:
            body = label
        if multi:
            box = _CHECKED if position in checked else _UNCHECKED
            _write_option_row(prefix=box, label=body, width=width, selected=is_selected)
        else:
            numbered = f"{position + 1}. {body}"
            marker = "❯" if is_selected else " "
            _write_option_row(prefix=marker, label=numbered, width=width, selected=is_selected)
    submit_selected = option_index == submit_row and not typing
    submit_marker = "❯" if submit_selected else " "
    _write_option_row(
        prefix=submit_marker,
        label=_SUBMIT,
        width=width,
        selected=submit_selected,
    )
    if typing:
        hint = _HINT_TYPING
    elif multi:
        hint = _HINT_MULTI
    else:
        hint = _HINT
    write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{hint}{ui_theme.ANSI_RESET}")
    sys.stdout.flush()


def _erase_ask_user(question: AskUserQuestion) -> None:
    erase_menu_lines(_menu_height(question))
    sys.stdout.flush()


def _leave_ask_user(question: AskUserQuestion) -> None:
    """Erase the menu and restore TTY so the next Rich line starts at column 0.

    Menu rows are padded to the full terminal width; without a column reset,
    later prints (``Selection cancelled``, ``/exit`` resume hint) stagger
    diagonally across the screen.
    """
    _erase_ask_user(question)
    leave_inline_menu()


def _next_unanswered(answers: list[str | None], start: int) -> int:
    n = len(answers)
    for offset in range(n):
        index = (start + offset) % n
        if answers[index] is None:
            return index
    return start


def _commit_multi(
    labels: list[str],
    checked: set[int],
    draft: str,
) -> str | None:
    """Join checked option labels (and custom draft when that row is checked)."""
    parts: list[str] = []
    for index in sorted(checked):
        if index < 0 or index >= len(labels):
            continue
        if labels[index] == CUSTOM_OPTION:
            text = draft.strip()
            if text:
                parts.append(text)
            continue
        parts.append(labels[index])
    if not parts:
        return None
    return "\n".join(parts)


def repl_ask_user(
    questions: tuple[AskUserQuestion, ...] | list[AskUserQuestion],
) -> tuple[str, ...] | None:
    """Show the Ask User wizard; return selected labels or None on Esc.

    Only call when :func:`repl_tty_interactive` is True. The custom row is
    edited in place inside the option array (concrete strings only in the
    result — never the sentinel label). Multi-select answers are newline-joined.
    """
    from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes

    items = tuple(questions)
    if len(items) < 2 or not repl_tty_interactive():
        return None
    clear_live_prompt_paint()
    drain_stale_cpr_bytes()
    hide_terminal_cursor()
    flush_pending_input()
    answers: list[str | None] = [None] * len(items)
    drafts: list[str] = [""] * len(items)
    checked_sets: list[set[int]] = [set() for _ in items]
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
        custom_index = len(labels) - 1
        on_custom = opt_idx == custom_index
        custom_draft = drafts[q_idx] if on_custom else None
        multi = question.multi_select
        _draw_ask_user(
            questions=items,
            current=q_idx,
            answers=answers,
            option_index=opt_idx,
            erase_lines=0 if first else current_height,
            custom_draft=custom_draft,
            checked=checked_sets[q_idx] if multi else None,
        )
        if first:
            flush_pending_input()
            first = False
        current_height = _menu_height(question)
        action = read_menu_or_char(allow_chars=on_custom)
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
        if on_custom and action == "backspace":
            drafts[q_idx] = drafts[q_idx][:-1]
            if multi and not drafts[q_idx].strip():
                checked_sets[q_idx].discard(custom_index)
            continue
        if on_custom and len(action) == 1 and action.isprintable() and action not in "\t\n\r":
            drafts[q_idx] += action
            if multi and drafts[q_idx].strip():
                checked_sets[q_idx].add(custom_index)
            continue

        submit_row = len(labels)

        if multi:
            toggle_index: int | None = None
            if action in (" ", "enter") and opt_idx < len(labels):
                toggle_index = opt_idx
            elif (not on_custom) and len(action) == 1 and action.isdigit():
                picked = int(action) - 1
                if 0 <= picked < len(labels):
                    toggle_index = picked
            if toggle_index is not None:
                if toggle_index == custom_index and not drafts[q_idx].strip():
                    opt_idx = toggle_index
                    option_focus = toggle_index
                    continue
                if toggle_index in checked_sets[q_idx]:
                    checked_sets[q_idx].discard(toggle_index)
                else:
                    checked_sets[q_idx].add(toggle_index)
                opt_idx = toggle_index
                option_focus = toggle_index
                continue
            if action == "enter" and opt_idx == submit_row:
                committed = _commit_multi(labels, checked_sets[q_idx], drafts[q_idx])
                if committed is None:
                    continue
                answers[q_idx] = committed
                if all(item is not None for item in answers):
                    _leave_ask_user(question)
                    return tuple(str(item) for item in answers)
                q_idx = _next_unanswered(answers, q_idx + 1)
                opt_idx = 0
                option_focus = 0
                continue
            if action in ("cancel", "eof"):
                _leave_ask_user(question)
                return None
            continue

        selected: int | None = None
        if action == "enter":
            if on_custom:
                text = drafts[q_idx].strip()
                if not text:
                    continue
                answers[q_idx] = text
                if all(item is not None for item in answers):
                    _leave_ask_user(question)
                    return tuple(str(item) for item in answers)
                q_idx = _next_unanswered(answers, q_idx + 1)
                opt_idx = 0
                option_focus = 0
                continue
            selected = option_focus if opt_idx == submit_row else opt_idx
            if selected < len(labels):
                option_focus = selected
        elif (not on_custom) and len(action) == 1 and action.isdigit():
            picked = int(action) - 1
            if 0 <= picked < len(labels):
                selected = picked

        if selected is not None:
            chosen = labels[selected]
            if chosen == CUSTOM_OPTION:
                opt_idx = selected
                option_focus = selected
                continue
            answers[q_idx] = chosen
            if all(item is not None for item in answers):
                _leave_ask_user(question)
                return tuple(str(item) for item in answers)
            q_idx = _next_unanswered(answers, q_idx + 1)
            opt_idx = 0
            option_focus = 0
            continue
        if action in ("cancel", "eof"):
            _leave_ask_user(question)
            return None
        # ignore / unmapped


def edit_custom_option_in_menu(
    *,
    title: str,
    choices: list[tuple[str, str]],
    custom_index: int,
    initial_draft: str = "",
) -> str | None:
    """Type on the custom row of an OpenSRE option array (same panel, Droid-style)."""
    labels = [label for _value, label in choices]
    draft = initial_draft
    height = 0
    first = True
    while True:
        display = list(labels)
        display[custom_index] = f"{draft}{_CURSOR}"
        width = menu_columns()
        if not first:
            erase_menu_lines(height)
        write_menu_line(
            f"{ui_theme.PROMPT_ACCENT_ANSI}{strip_terminal_controls(title)}{ui_theme.ANSI_RESET}"
        )
        write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{'─' * width}{ui_theme.ANSI_RESET}")
        for position, label in enumerate(display):
            numbered = f"{position + 1}. {label}"
            selected = position == custom_index
            marker = "❯" if selected else " "
            _write_option_row(
                prefix=marker,
                label=numbered,
                width=width,
                selected=selected,
            )
        write_menu_line(f"{ui_theme.DIM_COUNTER_ANSI}{_HINT_TYPING}{ui_theme.ANSI_RESET}")
        sys.stdout.flush()
        first = False
        height = 1 + 1 + len(display) + 1
        key = read_menu_or_char(allow_chars=True)
        if key == "enter":
            text = draft.strip()
            if text:
                erase_menu_lines(height)
                leave_inline_menu()
                return text
            continue
        if key in ("cancel", "eof"):
            erase_menu_lines(height)
            leave_inline_menu()
            return None
        if key == "backspace":
            draft = draft[:-1]
            continue
        if key in ("up", "down", "tab", "shift_tab", "left", "right", "ignore"):
            continue
        if len(key) == 1 and key.isprintable():
            draft += key


__all__ = [
    "CUSTOM_OPTION",
    "edit_custom_option_in_menu",
    "format_ask_user_breadcrumb",
    "repl_ask_user",
]

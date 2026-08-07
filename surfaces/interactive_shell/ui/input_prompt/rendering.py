"""Prompt text, hint, placeholder, and submitted-turn rendering."""

from __future__ import annotations

from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console
from rich.text import Text

from platform.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.banner.banner_state import integration_display_name
from surfaces.interactive_shell.ui.input_prompt.completion import completion_preview_hint_ansi
from surfaces.interactive_shell.ui.input_prompt.layout import (
    _clip_text,
    _prompt_line_width,
    _short_meta,
)

_PROMPT_RULE_CHAR = "─"
_DEFAULT_PLACEHOLDER_TEXT = "Type a message, /command, or paste an alert"
_DEFAULT_PLACEHOLDER_ANSI = ANSI(
    f"{ui_theme.ANSI_DIM}{_DEFAULT_PLACEHOLDER_TEXT}{ui_theme.ANSI_RESET}"
)


def _prompt_rule_line(width: int) -> str:
    return _PROMPT_RULE_CHAR * max(width, 1)


def _prompt_rule_ansi() -> str:
    # One column short of the terminal width so shrink-resize cannot soft-wrap
    # this line and orphan stale prompt frames in scrollback.
    return (
        f"{ui_theme.PROMPT_FRAME_ANSI}"
        f"{_prompt_rule_line(_prompt_line_width())}"
        f"{ui_theme.ANSI_RESET}"
    )


def _prompt_turn_number(session: Session) -> int:
    """1-based number for the prompt line currently being entered.

    Derived from the count of accepted submissions, never from
    ``session.history``: one request can append many history rows (shell
    commands, tool executions) but must advance the ``[N]`` label only once.
    """
    return session.terminal.submitted_turn_count + 1


def _counter_text(turn_number: int) -> str:
    return f"[{turn_number}] "


def _prompt_counter_text(session: Session) -> str:
    return _counter_text(_prompt_turn_number(session))


def _prompt_line_ansi(session: Session) -> ANSI:
    counter = _prompt_counter_text(session)
    prefix = f"{ui_theme.DIM_COUNTER_ANSI}{counter}{ui_theme.ANSI_RESET}"
    return ANSI(f"{prefix}{ui_theme.PROMPT_ACCENT_ANSI}❯{ui_theme.ANSI_RESET} ")


def _prompt_message(session: Session) -> ANSI:
    """Top border rule plus cursor line: the top two rows of the input box."""
    return ANSI(f"{_prompt_rule_ansi()}\n{_prompt_line_ansi(session).value}")


def render_submitted_prompt(console: Console, session: Session, text: str) -> None:
    """Render the submitted user turn above the streamed assistant response.

    Claims the turn's ``[N]`` number: every accepted submission (interactive or
    startup replay) passes through here exactly once, so the counter advances
    once per prompt line regardless of what the turn later records in history.
    """
    counter = _counter_text(session.terminal.claim_turn_number())
    lines = text.splitlines() or [""]
    continuation_prefix = " " * (len(counter) + len("❯ "))
    rendered = Text()
    # Rich's Style.parse() reads the bare str value of a _LazyRichStyle (""),
    # so resolve to a concrete string at the call site to keep palette colors.
    rendered.append(counter, style=str(ui_theme.DIM))
    rendered.append("❯ ", style=f"bold {ui_theme.HIGHLIGHT}")
    rendered.append(lines[0], style=str(ui_theme.TEXT))
    for line in lines[1:]:
        rendered.append("\n")
        rendered.append(continuation_prefix, style=str(ui_theme.DIM))
        rendered.append(line, style=str(ui_theme.TEXT))
    console.print(rendered)


def resolve_prompt_prefix_ansi(*, inline_spinner: str, idle_hint: str) -> str:
    """Choose the prompt's top context line: spinner, completion preview, or idle hint."""
    if inline_spinner:
        return inline_spinner
    preview = completion_preview_hint_ansi()
    return preview or idle_hint


def resolve_idle_hint_ansi(session: Session) -> str:
    """Dim hint line above the prompt rule: shortcuts plus connected integrations."""
    parts = ["/ for commands", "tab tool details", "↑↓ history"]
    if session.configured_integrations_known and session.configured_integrations:
        max_shown = 4
        names = [integration_display_name(name) for name in sorted(session.configured_integrations)]
        shown = names[:max_shown]
        overflow = len(names) - len(shown)
        integration_segment = " · ".join(shown)
        if overflow:
            integration_segment += f" +{overflow}"
        parts.append(integration_segment)
    app = get_app_or_none()
    if app is not None and app.current_buffer.text:
        parts.append("esc to clear")
    # Clip to the safe prompt-region width so a long integration list cannot
    # reach the last column and soft-wrap on shrink-resize.
    hint = _clip_text(" · ".join(parts), _prompt_line_width())
    return f"{ui_theme.DIM_ANSI}{hint}{ui_theme.ANSI_RESET}"


def resolve_prompt_placeholder(session: Session) -> ANSI:
    """Contextual ghost text when the input buffer is empty."""
    parts: list[str] = []
    if session.terminal.trust_mode:
        parts.append("trust on")
    running = session.task_registry.running_count()
    if running:
        parts.append(f"{running} task{'s' if running != 1 else ''} running")
    if session.resumed_from_name:
        parts.append(f"resumed: {_short_meta(session.resumed_from_name, max_len=32)}")
    if parts:
        return ANSI(f"{ui_theme.ANSI_DIM}{' · '.join(parts)}{ui_theme.ANSI_RESET}")
    return _DEFAULT_PLACEHOLDER_ANSI

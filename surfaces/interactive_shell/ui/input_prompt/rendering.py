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
from surfaces.interactive_shell.ui.input_prompt.layout import _short_meta, _terminal_columns

_PROMPT_RULE_CHAR = "─"
_DEFAULT_PLACEHOLDER_TEXT = "Type a message, /command, or paste an alert"
_DEFAULT_PLACEHOLDER_ANSI = ANSI(
    f"{ui_theme.ANSI_DIM}{_DEFAULT_PLACEHOLDER_TEXT}{ui_theme.ANSI_RESET}"
)


def _prompt_rule_line(width: int) -> str:
    return _PROMPT_RULE_CHAR * max(width, 1)


def _prompt_rule_ansi() -> str:
    return (
        f"{ui_theme.PROMPT_FRAME_ANSI}{_prompt_rule_line(_terminal_columns())}{ui_theme.ANSI_RESET}"
    )


def _prompt_turn_number(session: Session) -> int:
    """1-based index for the turn about to be entered or just submitted."""
    return len(session.history) + 1


def _prompt_counter_text(session: Session) -> str:
    return f"[{_prompt_turn_number(session)}] "


def _prompt_prefix_text(session: Session) -> str:
    return f"{_prompt_counter_text(session)}❯ "


def _prompt_line_ansi(session: Session) -> ANSI:
    counter = _prompt_counter_text(session)
    prefix = f"{ui_theme.DIM_COUNTER_ANSI}{counter}{ui_theme.ANSI_RESET}"
    return ANSI(f"{prefix}{ui_theme.PROMPT_ACCENT_ANSI}❯{ui_theme.ANSI_RESET} ")


def _prompt_message(session: Session) -> ANSI:
    """Top border rule plus cursor line: the top two rows of the input box."""
    return ANSI(f"{_prompt_rule_ansi()}\n{_prompt_line_ansi(session).value}")


def render_submitted_prompt(console: Console, session: Session, text: str) -> None:
    """Render the submitted user turn above the streamed assistant response."""
    lines = text.splitlines() or [""]
    continuation_prefix = " " * len(_prompt_prefix_text(session))
    rendered = Text()
    counter = _prompt_counter_text(session)
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
    parts = ["/ for commands", "↑↓ history"]
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
    hint = " · ".join(parts)
    return f"{ui_theme.DIM_ANSI}{hint}{ui_theme.ANSI_RESET}"


def resolve_prompt_placeholder(session: Session) -> ANSI:
    """Contextual ghost text when the input buffer is empty."""
    parts: list[str] = []
    if session.trust_mode:
        parts.append("trust on")
    running = session.task_registry.running_count()
    if running:
        parts.append(f"{running} task{'s' if running != 1 else ''} running")
    if session.resumed_from_name:
        parts.append(f"resumed: {_short_meta(session.resumed_from_name, max_len=32)}")
    if parts:
        return ANSI(f"{ui_theme.ANSI_DIM}{' · '.join(parts)}{ui_theme.ANSI_RESET}")
    return _DEFAULT_PLACEHOLDER_ANSI

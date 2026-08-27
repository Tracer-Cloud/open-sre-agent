"""Prompt text, hint, placeholder, and submitted-turn rendering."""

from __future__ import annotations

from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.handoff import parse_ask_user_answers
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.handoff_questions import (
    handoff_answer_style,
    last_assistant_asked_handoff,
    render_ask_user_qa,
    render_handoff_answer_marker,
)
from surfaces.interactive_shell.ui.input_prompt.completion import completion_preview_hint_ansi
from surfaces.interactive_shell.ui.input_prompt.layout import (
    _short_meta,
    clip_prompt_text,
    prompt_line_width,
)
from surfaces.shared.terminal.banner.banner_state import integration_display_name

_PROMPT_RULE_CHAR = "─"
DEFAULT_PLACEHOLDER_TEXT = "Type a message, /command, or paste an alert"
_DEFAULT_PLACEHOLDER_ANSI = ANSI(
    f"{ui_theme.ANSI_DIM}{DEFAULT_PLACEHOLDER_TEXT}{ui_theme.ANSI_RESET}"
)


def _prompt_rule_line(width: int) -> str:
    return _PROMPT_RULE_CHAR * max(width, 1)


def _prompt_rule_ansi() -> str:
    # One column short of the terminal width so shrink-resize cannot soft-wrap
    # this line and orphan stale prompt frames in scrollback.
    return (
        f"{ui_theme.PROMPT_FRAME_ANSI}{_prompt_rule_line(prompt_line_width())}{ui_theme.ANSI_RESET}"
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

    Autosubmitted lines (e.g. ``/goal set`` queuing the condition) get a dim
    ``↗ /goal`` marker so the work turn is visually distinct from the slash
    that attached the goal.
    """
    stripped = text.strip()
    # Internal exclusive-stdin turn — never echo ``/choose``. Clear the autosubmit
    # flag the queued ``/choose`` carried so a genuine turn after a cancelled menu
    # reads as a new workload (which resets the ask-user round counter).
    if stripped == "/choose" or stripped.startswith("/choose "):
        session.terminal.last_input_autosubmitted = False
        return
    is_handoff_answer = bool(session.terminal.awaiting_handoff_answer)
    if not is_handoff_answer:
        is_handoff_answer = last_assistant_asked_handoff(
            list(getattr(session, "cli_agent_messages", []) or [])
        )
    session.terminal.awaiting_handoff_answer = False
    ask_user_pairs = parse_ask_user_answers(stripped) if is_handoff_answer else []
    if len(ask_user_pairs) >= 2:
        # Keep the Ask User block in the transcript (Q white, A brand). Claim the
        # turn number so the next prompt still advances; do not paint a fake
        # ``[N] ❯`` — leave this as the Ask User card.
        session.terminal.claim_turn_number()
        render_ask_user_qa(console, ask_user_pairs)
        return
    autosubmitted = bool(session.terminal.last_input_autosubmitted)
    session.terminal.last_input_autosubmitted = False
    if is_handoff_answer and autosubmitted:
        # A fixed picker choice already has a compact persistent result. Do not
        # manufacture a second user turn in scrollback; only mark the synthetic
        # answer so a no-op model acknowledgement can be omitted as well.
        session.terminal.pending_choice_response = stripped
        return
    if is_handoff_answer:
        console.print(render_handoff_answer_marker())
    elif autosubmitted:
        # Keep this shorter than the condition — the ``[N] ❯`` line carries the
        # full text; this only answers "is this still /goal set or real work?".
        console.print(
            Text(
                "↗ /goal — work turn (condition auto-submitted)",
                style=str(ui_theme.DIM),
            )
        )
    counter = _counter_text(session.terminal.claim_turn_number())
    lines = text.splitlines() or [""]
    continuation_prefix = " " * (len(counter) + len("❯ "))
    rendered = Text()
    # Rich's Style.parse() reads the bare str value of a _LazyRichStyle (""),
    # so resolve to a concrete string at the call site to keep palette colors.
    body_style = handoff_answer_style() if is_handoff_answer else str(ui_theme.TEXT)
    rendered.append(counter, style=str(ui_theme.DIM))
    rendered.append("❯ ", style=f"bold {ui_theme.HIGHLIGHT}")
    rendered.append(lines[0], style=body_style)
    for line in lines[1:]:
        rendered.append("\n")
        rendered.append(continuation_prefix, style=str(ui_theme.DIM))
        rendered.append(line, style=body_style)
    console.print(rendered)


def resolve_prompt_prefix_ansi(*, inline_spinner: str, idle_hint: str) -> str:
    """Choose the prompt's top context line: spinner, completion preview, or idle hint."""
    if inline_spinner:
        return inline_spinner
    preview = completion_preview_hint_ansi()
    return preview or idle_hint


def resolve_idle_hint_ansi(session: Session) -> str:
    """Status line when no turn is running: ``Ready`` plus shortcuts.

    The live prompt always shows either ``Thinking…`` (spinner) or ``Ready``.
    Without ``Ready``, an in-progress plan overlay looks like the agent is
    still working after the turn has already returned the prompt.
    """
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
    width = prompt_line_width()
    status = "Ready"
    rest = clip_prompt_text(" · ".join(parts), max(width - len(status) - 3, 1))
    return (
        f"{ui_theme.PROMPT_ACCENT_ANSI}{status}{ui_theme.ANSI_RESET}"
        f"{ui_theme.DIM_ANSI} · {rest}{ui_theme.ANSI_RESET}"
    )


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
    if (
        session.task_plan is not None
        and session.task_plan.all_pending
        and session.plan_only_until_authorized
    ):
        return ANSI(
            f"{ui_theme.ANSI_DIM}say go to start the plan, or type a message{ui_theme.ANSI_RESET}"
        )
    return _DEFAULT_PLACEHOLDER_ANSI

"""Prompt text, hint, placeholder, and submitted-turn rendering."""

from __future__ import annotations

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

DEFAULT_PLACEHOLDER_TEXT = 'Try "Investigate this alert"'
_PLAN_CONTINUE_PLACEHOLDER = "continue the plan, or type a message"


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
    del session
    return ANSI(f" {ui_theme.PROMPT_ACCENT_ANSI}>{ui_theme.ANSI_RESET} ")


def _prompt_message(session: Session) -> ANSI:
    """Return the cursor line rendered inside the composer frame."""
    return _prompt_line_ansi(session)


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
    # Rich's Style.parse() reads the bare str value of a _LazyRichStyle (""),
    # so resolve to a concrete string at the call site to keep palette colors.
    # The user row is recessed grey (no bright accent): the agent's ``∴`` reply
    # and working notes carry the visual weight, and SECONDARY keeps the ask
    # readable while sitting a shade above the darker DIM notes so the three
    # turn roles still read apart. The ``[N] ❯`` prefix is dimmer still.
    body_style = handoff_answer_style() if is_handoff_answer else str(ui_theme.SECONDARY)
    prefix_style = str(ui_theme.DIM)
    continuation_prefix = " " * (len(counter) + len("❯ "))
    rendered = Text()
    for index, line in enumerate(lines):
        if index:
            rendered.append("\n")
        if index == 0:
            rendered.append(counter, style=prefix_style)
            rendered.append("❯ ", style=prefix_style)
        else:
            rendered.append(continuation_prefix, style=prefix_style)
        rendered.append(line, style=body_style)
    console.print(rendered)


def resolve_prompt_prefix_ansi(*, inline_spinner: str, idle_hint: str) -> str:
    """Choose the prompt's top context line: spinner, completion preview, or idle hint."""
    if inline_spinner:
        return inline_spinner
    preview = completion_preview_hint_ansi()
    return preview or idle_hint


def resolve_idle_hint_ansi(session: Session) -> str:
    """Return the idle spacer used by the fixed-height prompt region."""
    del session
    return ""


def ctrl_c_exit_hint_ansi() -> str:
    """Return the transient double-press exit hint for the fixed status row."""
    return f"{ui_theme.DIM_ANSI}(Press Ctrl+C again to exit){ui_theme.ANSI_RESET}"


def composer_footer_ansi() -> str:
    """Return the help hint and terminal-mode label below the composer."""
    left = "? for help"
    right = "TERMINAL ■"
    width = prompt_line_width()
    if len(left) + len(right) + 2 > width:
        clipped = clip_prompt_text(left, width)
        return f"{ui_theme.DIM_ANSI}{clipped}{ui_theme.ANSI_RESET}"
    pad = width - len(left) - len(right)
    return (
        f"{ui_theme.DIM_ANSI}{left}{ui_theme.ANSI_RESET}"
        f"{' ' * pad}{ui_theme.BRAND_ANSI}TERMINAL "
        f"{ui_theme.HIGHLIGHT_ANSI}■{ui_theme.ANSI_RESET}"
    )


def resolve_prompt_placeholder(session: Session) -> ANSI:
    """Contextual ghost text when the input buffer is empty.

    Built per redraw (not at import) so theme ANSI cannot freeze stale, and so
    an unfinished live plan can replace the default Investigate hint.
    """
    parts: list[str] = []
    if session.terminal.trust_mode:
        parts.append("trust on")
    running = session.task_registry.running_count()
    if running:
        parts.append(f"{running} task{'s' if running != 1 else ''} running")
    if session.resumed_from_name:
        parts.append(f"resumed: {_short_meta(session.resumed_from_name, max_len=32)}")
    if parts:
        return ANSI(f"{ui_theme.DIM_ANSI}{' · '.join(parts)}{ui_theme.ANSI_RESET}")
    if (
        session.task_plan is not None
        and session.task_plan.all_pending
        and session.plan_only_until_authorized
    ):
        return ANSI(
            f"{ui_theme.DIM_ANSI}say go to start the plan, or type a message{ui_theme.ANSI_RESET}"
        )
    plan = session.task_plan
    if (
        plan is not None
        and plan.steps
        and not plan.all_completed
        and not session.plan_only_until_authorized
    ):
        return ANSI(f"{ui_theme.DIM_ANSI}{_PLAN_CONTINUE_PLACEHOLDER}{ui_theme.ANSI_RESET}")
    return ANSI(f"{ui_theme.DIM_ANSI}{DEFAULT_PLACEHOLDER_TEXT}{ui_theme.ANSI_RESET}")

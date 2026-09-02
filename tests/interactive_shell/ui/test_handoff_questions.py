"""Human hand-off questions vs answers are styled differently."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.handoff_questions import (
    is_handoff_question,
    last_assistant_asked_handoff,
    render_ask_user_qa,
    render_choice_selection,
    render_handoff_question,
    try_render_ask_user_submission,
)
from surfaces.interactive_shell.ui.input_prompt.rendering import render_submitted_prompt
from surfaces.interactive_shell.ui.streaming.renderer import render_markdown_block


def test_short_closing_question_is_a_handoff() -> None:
    assert is_handoff_question("Which environment should I investigate first?")
    assert is_handoff_question("**Want me to:** run a full investigation?")
    assert not is_handoff_question("checkout is returning 502s")
    assert not is_handoff_question("### [1/8] Prerequisite checks")


def test_render_markdown_block_highlights_a_question() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_markdown_block(console, "Which environment should I investigate first?")
    output = buffer.getvalue()
    assert "?" in output
    assert "Which environment should I investigate first?" in output


def test_submitted_answer_to_a_handoff_is_marked() -> None:
    session = Session()
    session.cli_agent_messages = [
        ("user", "checkout is 502ing"),
        ("assistant", "Which environment should I investigate first?"),
    ]
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    assert last_assistant_asked_handoff(list(session.cli_agent_messages))
    render_submitted_prompt(console, session, "staging")
    output = buffer.getvalue()
    assert "↗ answer" in output
    assert "staging" in output
    # The marker hugs the assistant answer it responds to (no blank row above it),
    # and the between-turns gap falls after the marker, before the input row.
    assert not output.startswith("\n")
    lines = output.splitlines()
    marker_index = next(i for i, line in enumerate(lines) if "↗ answer" in line)
    input_index = next(i for i, line in enumerate(lines) if "staging" in line)
    assert lines[marker_index + 1].strip() == ""
    assert marker_index < input_index


def test_ask_user_answers_render_as_numbered_qa() -> None:
    session = Session()
    session.terminal.awaiting_handoff_answer = True
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    text = (
        "1. Where does the /api/orders service live?\n"
        "Hypothetical/demo scenario, no real code\n"
        "\n"
        "2. What's the time window of the p99 regression?\n"
        "Last 7 days"
    )
    render_submitted_prompt(console, session, text)
    output = buffer.getvalue()
    assert "Ask User" in output
    assert "↗ You answered" not in output
    assert "Where does the /api/orders service live?" in output
    assert "Hypothetical/demo scenario, no real code" in output
    assert "Last 7 days" in output
    assert session.terminal.submitted_turn_count == 1


def test_ask_user_qa_highlights_answer_differently_from_question() -> None:
    # 10.6 core contract: the answer must render in a distinct colour from the
    # question, under a highlighted "Ask User" header, so a filled-in recap reads
    # apart at a glance. Pin the colours, not just the text.
    from infrastructure.terminal import theme as ui_theme

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, color_system="truecolor", highlight=False)
    render_ask_user_qa(console, [("Which product should I demo?", "OpenSRE itself")])
    raw = buffer.getvalue()

    def _sgr(hex_color: str) -> str:
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
        return f"38;2;{r};{g};{b}"

    question_sgr = _sgr(str(ui_theme.TEXT))
    answer_sgr = _sgr(str(ui_theme.BRAND))
    header_sgr = _sgr(str(ui_theme.HIGHLIGHT))
    assert question_sgr != answer_sgr  # the two colours genuinely differ
    assert header_sgr in raw  # "Ask User" header in the accent colour
    assert f"1;{question_sgr}" in raw  # question is bold TEXT (droid-style emphasis)
    assert answer_sgr in raw  # answer highlighted in BRAND, distinct from the question


def test_ask_user_qa_leaves_blank_rows_between_pairs() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_ask_user_qa(
        console,
        [
            ("How large is the p99 regression on /api/orders?", "Percentage increase only"),
            ("When did the regression begin?", "Sudden without known change"),
        ],
    )
    lines = [line.rstrip() for line in buffer.getvalue().splitlines()]
    header = next(index for index, line in enumerate(lines) if line.strip() == "Ask User")
    assert lines[header + 1] == ""
    first_answer = next(
        index for index, line in enumerate(lines) if "Percentage increase only" in line
    )
    assert lines[first_answer + 1] == ""
    assert "When did the regression begin?" in lines[first_answer + 2]


def test_choose_slash_is_not_echoed() -> None:
    session = Session()
    session.terminal.awaiting_handoff_answer = True
    session.terminal.last_input_autosubmitted = True
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_submitted_prompt(console, session, "/choose")
    assert session.terminal.awaiting_handoff_answer is True
    assert buffer.getvalue() == ""
    assert session.terminal.submitted_turn_count == 0
    # The queued /choose must not leave a stale autosubmit flag, or the next
    # genuine turn is misread as autosubmitted and skips the round-counter reset.
    assert session.terminal.last_input_autosubmitted is False


def test_auto_submitted_single_choice_is_not_echoed_as_a_user_turn() -> None:
    session = Session()
    session.terminal.awaiting_handoff_answer = True
    session.terminal.last_input_autosubmitted = True
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)

    render_submitted_prompt(console, session, "Blue-green")

    assert buffer.getvalue() == ""
    assert session.terminal.submitted_turn_count == 0
    assert session.terminal.pending_choice_response == "Blue-green"


def test_choice_selection_strips_terminal_controls() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)

    render_choice_selection(console, "Deploy?\x1b]0;pwn\x07", "Canary\x1b[2K")

    output = buffer.getvalue()
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "✓ Deploy?" in output
    assert "Canary" in output


def test_multi_select_choice_indents_every_selected_line() -> None:
    # Arrange: a multi-select answer arrives as one option per line.
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    answer = "Audit the architecture\nFind failing PRs\nRemediate alerts"

    # Act
    render_choice_selection(console, "Select Complex Demos", answer)

    # Assert: heading, then every option indented under it — never flush-left.
    lines = [line.rstrip() for line in buffer.getvalue().splitlines() if line.strip()]
    assert "✓ Select Complex Demos" in lines
    for label in ("Audit the architecture", "Find failing PRs", "Remediate alerts"):
        assert f"  {label}" in lines
        assert label not in lines


def test_try_render_rejects_a_single_choice_label() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    assert try_render_ask_user_submission(console, "Commit the changes") is False
    assert buffer.getvalue() == ""


def test_render_handoff_question_strips_terminal_controls() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_handoff_question(console, "Which env?\x1b]0;pwn\x07\x1b[2K staging")
    output = buffer.getvalue()
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "Which env?" in output
    assert "staging" in output

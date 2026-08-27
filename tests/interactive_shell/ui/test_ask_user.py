"""Ask User wizard: breadcrumb and key loop."""

from __future__ import annotations

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
    parse_ask_user_answers,
)
from surfaces.interactive_shell.ui.ask_user import format_ask_user_breadcrumb, repl_ask_user

_QUESTIONS = (
    AskUserQuestion(
        label="Codebase",
        title="Where does the /api/orders service live?",
        options=("Hypothetical/demo scenario, no real code", "I'll point you at a repo"),
    ),
    AskUserQuestion(
        label="Metrics",
        title="How should I get the p99 latency data?",
        options=("I'll paste the raw numbers/graph description", "Query Datadog"),
    ),
    AskUserQuestion(
        label="Window",
        title="What's the time window of the p99 regression?",
        options=("Last 7 days", "Last 24 hours"),
    ),
)


def test_breadcrumb_hollow_until_a_question_is_replied() -> None:
    # The current question is not filled just for being current — only replies fill it.
    crumb = format_ask_user_breadcrumb(
        _QUESTIONS,
        answered=(False, False, False),
    )
    assert crumb == "○ Codebase → ○ Metrics → ○ Window"


def test_breadcrumb_fills_only_replied_questions() -> None:
    # Codebase is replied (●); Metrics unanswered stays ○.
    crumb = format_ask_user_breadcrumb(
        _QUESTIONS,
        answered=(True, False, False),
    )
    assert crumb == "● Codebase → ○ Metrics → ○ Window"


def test_answer_block_round_trips() -> None:
    answers = (
        "Hypothetical/demo scenario, no real code",
        "I'll paste the raw numbers/graph description",
        "Last 7 days",
    )
    text = format_ask_user_answers(_QUESTIONS, answers)
    parsed = parse_ask_user_answers(text)
    assert parsed == list(zip((q.title for q in _QUESTIONS), answers, strict=True))


def test_wizard_enter_on_each_question_submits(monkeypatch) -> None:
    actions = iter(["enter", "enter", "enter"])
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.repl_tty_interactive",
        lambda: True,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._read_wizard_action",
        lambda: next(actions),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._draw_ask_user",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._erase_ask_user",
        lambda _question: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.flush_pending_input",
        lambda: None,
    )

    picked = repl_ask_user(_QUESTIONS)
    assert picked == (
        "Hypothetical/demo scenario, no real code",
        "I'll paste the raw numbers/graph description",
        "Last 7 days",
    )


def test_wizard_esc_cancels(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.repl_tty_interactive",
        lambda: True,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._read_wizard_action",
        lambda: "cancel",
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._draw_ask_user",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._erase_ask_user",
        lambda _question: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.flush_pending_input",
        lambda: None,
    )

    assert repl_ask_user(_QUESTIONS) is None


def test_wizard_flushes_leftover_keys_before_reading(monkeypatch) -> None:
    flushed = {"count": 0}

    def _flush() -> None:
        flushed["count"] += 1

    actions = iter(["enter", "enter", "enter"])
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.repl_tty_interactive",
        lambda: True,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._read_wizard_action",
        lambda: next(actions),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._draw_ask_user",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._erase_ask_user",
        lambda _question: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr("surfaces.interactive_shell.ui.ask_user.flush_pending_input", _flush)

    assert repl_ask_user(_QUESTIONS) is not None
    # Once before the loop, once after the first draw — leftover Enter from
    # the previous prompt must not auto-select option 1 on every question.
    assert flushed["count"] >= 2


def test_wizard_submit_row_confirms_highlighted_option(monkeypatch) -> None:
    """Arrow to Submit then Enter uses the option that was highlighted."""
    # First question has 2 options + custom = 3 rows + Submit. Up from the
    # first option wraps onto Submit without changing the highlighted option.
    actions = iter(["up", "enter", "enter", "enter"])
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.repl_tty_interactive",
        lambda: True,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._read_wizard_action",
        lambda: next(actions),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._draw_ask_user",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._erase_ask_user",
        lambda _question: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.flush_pending_input",
        lambda: None,
    )

    picked = repl_ask_user(_QUESTIONS)
    assert picked == (
        "Hypothetical/demo scenario, no real code",
        "I'll paste the raw numbers/graph description",
        "Last 7 days",
    )

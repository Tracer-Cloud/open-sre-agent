"""Tests for the suggested-loops startup picker."""

from __future__ import annotations

import pytest

from core.agent_harness.prompts.skills_loader import list_action_skills
from surfaces.interactive_shell.runtime.startup import loop_suggestions as ls
from surfaces.interactive_shell.session import Session


class _EventRecorder:
    """Named capture fakes so tests can assert exactly which events fired."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.prompted = 0
        self.skipped = 0
        self.selected: list[str] = []
        monkeypatch.setattr(ls, "capture_loop_suggestion_prompted", self._prompted)
        monkeypatch.setattr(ls, "capture_loop_suggestion_skipped", self._skipped)
        monkeypatch.setattr(ls, "capture_loop_suggestion_selected", self._selected)

    def _prompted(self) -> None:
        self.prompted += 1

    def _skipped(self) -> None:
        self.skipped += 1

    def _selected(self, *, option: str) -> None:
        self.selected.append(option)


def _force_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every gate input so the picker would be offered."""
    monkeypatch.setattr(ls, "is_test_run", lambda: False)
    monkeypatch.setattr(ls, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(ls, "_no_loops_configured", lambda: True)


# ── gate ─────────────────────────────────────────────────────────────────────


def test_gate_offered_when_all_conditions_met(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_offered(monkeypatch)
    assert ls.should_offer_loop_suggestions() is True


def test_gate_skipped_in_test_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_offered(monkeypatch)
    monkeypatch.setattr(ls, "is_test_run", lambda: True)
    assert ls.should_offer_loop_suggestions() is False


def test_gate_skipped_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_offered(monkeypatch)
    monkeypatch.setattr(ls, "repl_tty_interactive", lambda: False)
    assert ls.should_offer_loop_suggestions() is False


def test_gate_skipped_when_loops_already_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_offered(monkeypatch)
    monkeypatch.setattr(ls, "_no_loops_configured", lambda: False)
    assert ls.should_offer_loop_suggestions() is False


def test_no_loops_configured_reads_scheduler_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.scheduler.store.list_tasks", lambda: [])
    assert ls._no_loops_configured() is True
    monkeypatch.setattr("platform.scheduler.store.list_tasks", lambda: [object()])
    assert ls._no_loops_configured() is False


# ── picker behavior ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("suggestion", ls.LOOP_SUGGESTIONS, ids=lambda s: s.option)
def test_selection_queues_prompt_and_captures_event(
    monkeypatch: pytest.MonkeyPatch, suggestion: ls.LoopSuggestion
) -> None:
    _force_offered(monkeypatch)
    events = _EventRecorder(monkeypatch)
    monkeypatch.setattr(ls, "repl_choose_one", lambda **_kw: suggestion.option)
    session = Session()

    ls.offer_loop_suggestions(session)

    assert session.terminal.pending_prompt_default == suggestion.prompt
    assert session.terminal.pending_prompt_autosubmit is True
    assert events.prompted == 1
    assert events.selected == [suggestion.option]
    assert events.skipped == 0


def test_escape_captures_skipped_and_queues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_offered(monkeypatch)
    events = _EventRecorder(monkeypatch)
    monkeypatch.setattr(ls, "repl_choose_one", lambda **_kw: None)
    session = Session()

    ls.offer_loop_suggestions(session)

    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False
    assert events.prompted == 1
    assert events.skipped == 1
    assert events.selected == []


def test_gate_closed_shows_no_menu_and_no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_offered(monkeypatch)
    monkeypatch.setattr(ls, "_no_loops_configured", lambda: False)
    events = _EventRecorder(monkeypatch)

    def _fail_menu(**_kw: object) -> str:
        raise AssertionError("menu must not render when the gate is closed")

    monkeypatch.setattr(ls, "repl_choose_one", _fail_menu)
    session = Session()

    ls.offer_loop_suggestions(session)

    assert session.terminal.pending_prompt_default is None
    assert events.prompted == 0
    assert events.skipped == 0
    assert events.selected == []


def test_picker_failure_never_blocks_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_offered(monkeypatch)
    _EventRecorder(monkeypatch)

    def _broken_menu(**_kw: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(ls, "repl_choose_one", _broken_menu)
    session = Session()

    ls.offer_loop_suggestions(session)  # must not raise

    assert session.terminal.pending_prompt_default is None


# ── catalog invariants ───────────────────────────────────────────────────────


def test_suggestions_are_unique_and_complete() -> None:
    options = [suggestion.option for suggestion in ls.LOOP_SUGGESTIONS]
    assert options == [ls.OPTION_CI_CD, ls.OPTION_TASK_MANAGEMENT, ls.OPTION_DAILY_BRIEF]
    assert len(set(options)) == len(options)
    for suggestion in ls.LOOP_SUGGESTIONS:
        assert suggestion.label.strip()
        assert suggestion.prompt.strip()


def test_backing_skills_exist_in_skills_index() -> None:
    """The canned prompts rely on these bundled skills staying discoverable."""
    skill_names = {skill.name for skill in list_action_skills()}
    assert {"github-ci-fix", "github-cli", "morning-report"} <= skill_names

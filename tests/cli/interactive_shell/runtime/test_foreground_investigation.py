"""Tests for shared foreground investigation lifecycle."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.runtime.foreground_investigation import run_foreground_investigation
from app.cli.interactive_shell.runtime.tasks import TaskRecord


def test_foreground_investigation_prompts_feedback_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feedback_calls: list[dict[str, object]] = []

    def _fake_feedback(final_state: dict[str, object], **kwargs: object) -> None:
        feedback_calls.append(dict(final_state))

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.feedback.prompt_investigation_feedback",
        _fake_feedback,
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.key_reader.restore_stdin_terminal",
        lambda: None,
    )

    session = ReplSession()
    console = Console(file=StringIO(), force_terminal=False)

    def _run(_task: TaskRecord) -> dict[str, str]:
        return {"root_cause": "db unreachable", "alert_name": "payments_etl"}

    final_state = run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate generic",
        run=_run,
        exception_context="test.foreground_investigation",
    )

    assert final_state == {"root_cause": "db unreachable", "alert_name": "payments_etl"}
    assert session.last_state == final_state
    assert feedback_calls == [final_state]


def test_foreground_investigation_skips_feedback_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feedback_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.feedback.prompt_investigation_feedback",
        lambda *_a, **_k: feedback_calls.append({}),
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.key_reader.restore_stdin_terminal",
        lambda: None,
    )

    session = ReplSession()
    console = Console(file=StringIO(), force_terminal=False)

    def _run(_task: TaskRecord) -> dict[str, str]:
        raise RuntimeError("boom")

    result = run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate generic",
        run=_run,
        exception_context="test.foreground_investigation",
    )

    assert result is None
    assert feedback_calls == []

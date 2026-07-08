"""Tests for structured investigation outcomes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.llm.shared.llm_retry import LLMCreditExhaustedError
from platform.common.errors import OpenSREError
from platform.common.task_types import TaskRecord
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.foreground_investigation import run_foreground_investigation
from surfaces.interactive_shell.ui.investigation_outcome import (
    classify_investigation_failure,
    normalize_investigation_target,
    user_facing_error_message,
)


def test_normalize_investigation_target_template() -> None:
    assert normalize_investigation_target("generic") == "generic"
    assert normalize_investigation_target("template:datadog") == "datadog"


def test_normalize_investigation_target_file_path() -> None:
    assert normalize_investigation_target(
        "alerts/checkout.json", path=Path("alerts/checkout.json")
    ) == ("checkout.json")


def test_classify_integration_failure() -> None:
    category, integration, _detail = classify_investigation_failure(
        RuntimeError("grafana query failed: 401 unauthorized")
    )
    assert category == "integration"
    assert integration == "grafana"


def test_user_facing_error_message_includes_suggestion() -> None:
    message = user_facing_error_message(
        OpenSREError("jenkins is not configured", suggestion="Run /integrations setup jenkins")
    )
    assert "jenkins is not configured" in message
    assert "Suggestion:" in message


def test_run_foreground_investigation_early_cancel_omits_stale_investigation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.last_investigation_id = "inv-old"
    console = Console(force_terminal=False, color_system=None, highlight=False)
    task = MagicMock(spec=TaskRecord)
    task.cancel_requested = False
    monkeypatch.setattr(
        session.task_registry,
        "create",
        lambda *_args, **_kwargs: task,
    )

    def _raise_interrupt(_task: TaskRecord) -> dict[str, object]:
        raise KeyboardInterrupt

    outcome = run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate generic",
        run=_raise_interrupt,
        exception_context="test",
        target="generic",
    )

    assert outcome.status == "cancelled"
    assert outcome.investigation_id == ""
    task.mark_cancelled.assert_called_once()


def test_run_foreground_investigation_credit_exhausted_shows_auth_login_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = Session()
    console = Console(force_terminal=False, color_system=None, highlight=False)
    task = MagicMock(spec=TaskRecord)
    task.cancel_requested = False
    monkeypatch.setattr(
        session.task_registry,
        "create",
        lambda *_args, **_kwargs: task,
    )

    def _raise_credit_exhausted(_task: TaskRecord) -> dict[str, object]:
        raise LLMCreditExhaustedError(
            "Anthropic credit exhausted (provider billing/quota). Original error: 400"
        )

    outcome = run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate alert.json",
        run=_raise_credit_exhausted,
        exception_context="test",
        target="alert.json",
    )

    output = capsys.readouterr().out
    assert outcome.status == "failed"
    assert "/model" in output
    assert "/auth login" in output
    task.mark_failed.assert_called_once()


def test_run_foreground_investigation_opensre_error_does_not_duplicate_auth_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = Session()
    console = Console(force_terminal=False, color_system=None, highlight=False)
    task = MagicMock(spec=TaskRecord)
    task.cancel_requested = False
    monkeypatch.setattr(
        session.task_registry,
        "create",
        lambda *_args, **_kwargs: task,
    )

    def _raise_credit_exhausted_opensre_error(_task: TaskRecord) -> dict[str, object]:
        raise OpenSREError(
            "Anthropic credit exhausted (provider billing/quota). Original error: 400",
            suggestion=(
                "Run /auth login <provider> to re-authenticate or add a different provider."
            ),
        )

    outcome = run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate alert.json",
        run=_raise_credit_exhausted_opensre_error,
        exception_context="test",
        target="alert.json",
    )

    output = capsys.readouterr().out
    assert outcome.status == "failed"
    assert output.count("/auth login") == 1
    task.mark_failed.assert_called_once()


def test_run_foreground_investigation_skips_feedback_when_pt_app_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#3690: prompt_app is on session.terminal; must not run raw stdin menu while active."""
    session = Session()
    session.terminal.prompt_app = MagicMock(is_running=True)
    console = Console(force_terminal=False, color_system=None, highlight=False)
    task = MagicMock(spec=TaskRecord)
    task.cancel_requested = False
    monkeypatch.setattr(
        session.task_registry,
        "create",
        lambda *_args, **_kwargs: task,
    )
    feedback = MagicMock()
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.feedback.prompt_investigation_feedback",
        feedback,
    )

    outcome = run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate grafana",
        run=lambda _task: {"root_cause": "sample"},
        exception_context="test",
        target="grafana",
    )

    assert outcome.status == "completed"
    feedback.assert_not_called()


def test_run_foreground_investigation_prompts_feedback_when_pt_app_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.terminal.prompt_app = MagicMock(is_running=False)
    console = Console(force_terminal=False, color_system=None, highlight=False)
    task = MagicMock(spec=TaskRecord)
    task.cancel_requested = False
    monkeypatch.setattr(
        session.task_registry,
        "create",
        lambda *_args, **_kwargs: task,
    )
    feedback = MagicMock()
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.feedback.prompt_investigation_feedback",
        feedback,
    )

    run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate",
        run=lambda _task: {"root_cause": "sample"},
        exception_context="test",
        target="",
    )

    feedback.assert_called_once_with({"root_cause": "sample"})


def test_run_foreground_investigation_skips_feedback_on_headless_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway SessionCore must not block on the RCA feedback picker."""
    from core.agent_harness.session import SessionCore
    from core.agent_harness.session.persistence.memory import InMemorySessionStorage

    session = SessionCore(storage=InMemorySessionStorage())
    console = Console(force_terminal=False, color_system=None, highlight=False)
    task = MagicMock(spec=TaskRecord)
    task.cancel_requested = False
    monkeypatch.setattr(
        session.task_registry,
        "create",
        lambda *_args, **_kwargs: task,
    )
    feedback = MagicMock()
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.feedback.prompt_investigation_feedback",
        feedback,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.choice_menu.repl_tty_interactive",
        lambda: True,
    )

    outcome = run_foreground_investigation(
        session=session,
        console=console,
        task_command="/investigate grafana",
        run=lambda _task: {"root_cause": "sample"},
        exception_context="test",
        target="grafana",
    )

    assert outcome.status == "completed"
    feedback.assert_not_called()

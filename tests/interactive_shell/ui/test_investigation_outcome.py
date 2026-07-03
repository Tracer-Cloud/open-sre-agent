"""Tests for structured investigation outcomes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import Session
from platform.common.errors import OpenSREError
from platform.common.task_types import TaskRecord
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

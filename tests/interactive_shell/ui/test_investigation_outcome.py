"""Tests for structured investigation outcomes."""

from __future__ import annotations

from pathlib import Path

from platform.common.errors import OpenSREError
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

"""Intake noise gate: explicit user requests are never discarded as noise."""

from __future__ import annotations

from typing import Any, cast

import pytest

import tools.investigation.stages.intake.node as intake_node
from core.domain.alerts.extraction import fallback_details
from core.state import InvestigationState

_CHATTY_MESSAGE = "thanks, looks good!"


def _noise_details(state: dict[str, Any]) -> Any:
    return fallback_details(state, state["raw_alert"]).model_copy(update={"is_noise": True})


def test_noise_without_user_request_skips_investigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: an unsolicited chatty message classified as noise.
    state = cast(
        InvestigationState,
        {"raw_alert": {"alert_name": "Interactive session", "message": _CHATTY_MESSAGE}},
    )
    monkeypatch.setattr(intake_node, "_extract_alert_details", _noise_details)

    # Act
    updates = intake_node.extract_alert(state)

    # Assert: the pipeline stops here.
    assert updates == {"is_noise": True}


def test_user_requested_investigation_bypasses_noise_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the same noise-classified text, but the user explicitly asked
    # for this investigation (typed investigate / accepted the offer).
    state = cast(
        InvestigationState,
        {
            "raw_alert": {"alert_name": "Interactive session", "message": _CHATTY_MESSAGE},
            "user_requested": True,
        },
    )
    monkeypatch.setattr(intake_node, "_extract_alert_details", _noise_details)

    # Act
    updates = intake_node.extract_alert(state)

    # Assert: the investigation proceeds with extracted alert fields.
    assert updates["is_noise"] is False
    assert updates["alert_name"]
    assert "problem_md" in updates

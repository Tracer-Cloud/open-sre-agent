"""Tests for investigation initial-state construction."""

from __future__ import annotations

from tools.investigation.state_factory import make_initial_state


def test_initial_state_defaults_to_unsolicited() -> None:
    state = make_initial_state(raw_alert={"alert_name": "A", "message": "cpu high"})

    assert state["user_requested"] is False


def test_initial_state_carries_explicit_user_request() -> None:
    state = make_initial_state(
        raw_alert={"alert_name": "A", "message": "cpu high"},
        user_requested=True,
    )

    assert state["user_requested"] is True

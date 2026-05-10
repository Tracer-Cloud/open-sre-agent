"""Tests for ReplSession state."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.constants as const_module
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind, TaskRegistry


class TestReplSession:
    def test_defaults(self) -> None:
        session = ReplSession()
        assert session.history == []
        assert session.last_state is None
        assert session.accumulated_context == {}
        assert session.trust_mode is False
        assert session.task_registry.list_recent() == []
        assert session.terminal_turn_count == 0
        assert session.terminal_fallback_count == 0
        assert session.ctrl_c_intervention_count == 0
        assert session.correction_intervention_count == 0

    def test_record_appends_entry(self) -> None:
        session = ReplSession()
        session.record("alert", "cpu high")
        session.record("slash", "/status", ok=True)
        session.record("alert", "bad one", ok=False)
        assert len(session.history) == 3
        assert session.history[-1]["type"] == "alert"
        assert session.history[-1]["ok"] is False

    def test_mark_latest_updates_most_recent_matching_kind(self) -> None:
        session = ReplSession()
        session.record("slash", "/investigate missing.json")
        session.record("alert", "missing.json", ok=False)

        session.mark_latest(ok=False, kind="slash")

        assert session.history[0]["ok"] is False
        assert session.history[1]["ok"] is False

    def test_clear_preserves_trust_mode(self) -> None:
        session = ReplSession()
        session.trust_mode = True
        session.accumulated_context["service"] = "api"
        session.record("alert", "something")
        session.last_state = {"foo": "bar"}
        session.cli_agent_messages.append(("user", "hey"))
        session.record_intervention("ctrl_c")
        session.record_intervention("correction")

        assert session.history_generation == 0
        session.clear()
        assert session.history_generation == 1

        assert session.history == []
        assert session.last_state is None
        assert session.accumulated_context == {}
        assert session.cli_agent_messages == []
        assert session.task_registry.list_recent() == []
        assert session.ctrl_c_intervention_count == 0
        assert session.correction_intervention_count == 0
        assert session.trust_mode is True  # preserved intentionally

    def test_clear_keeps_persisted_task_history_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = ReplSession()
        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        session.task_registry = TaskRegistry.persistent()
        task = session.task_registry.create(
            TaskKind.SYNTHETIC_TEST, command="opensre tests synthetic"
        )
        task.mark_running()

        session.clear()

        reloaded = TaskRegistry.persistent()
        loaded = reloaded.get(task.task_id)
        assert loaded is not None
        assert loaded.task_id == task.task_id

    def test_accumulate_from_state_extracts_known_keys(self) -> None:
        session = ReplSession()
        session.accumulate_from_state(
            {
                "service": "orders-api",
                "pipeline_name": "events_fact",
                "cluster_name": "prod-us-east",
                "region": "us-east-1",
                "environment": "production",
                "root_cause": "disk full",  # not accumulated
                "evidence": {"ev-1": "x"},  # not accumulated
            }
        )
        assert session.accumulated_context == {
            "service": "orders-api",
            "pipeline_name": "events_fact",
            "cluster_name": "prod-us-east",
            "region": "us-east-1",
            "environment": "production",
        }

    def test_accumulate_from_state_skips_empty_and_none(self) -> None:
        session = ReplSession()
        session.accumulate_from_state(
            {
                "service": "",
                "cluster_name": None,
                "region": "us-east-1",
            }
        )
        assert session.accumulated_context == {"region": "us-east-1"}

    def test_accumulate_from_state_merges_across_calls(self) -> None:
        """Subsequent investigations fill in context the earlier one didn't have."""
        session = ReplSession()
        session.accumulate_from_state({"service": "orders-api"})
        session.accumulate_from_state({"cluster_name": "prod-us-east"})
        assert session.accumulated_context == {
            "service": "orders-api",
            "cluster_name": "prod-us-east",
        }

    def test_accumulate_from_state_handles_none_and_empty_state(self) -> None:
        session = ReplSession()
        session.accumulate_from_state(None)
        session.accumulate_from_state({})
        assert session.accumulated_context == {}

    def test_record_terminal_turn_updates_aggregates(self) -> None:
        session = ReplSession()

        first = session.record_terminal_turn(
            executed_count=2,
            executed_success_count=1,
            fallback_to_llm=True,
        )
        second = session.record_terminal_turn(
            executed_count=1,
            executed_success_count=1,
            fallback_to_llm=False,
        )

        assert first.turn_index == 1
        assert first.fallback_count == 1
        assert first.action_success_percent == 50.0
        assert first.fallback_rate_percent == 100.0

        assert second.turn_index == 2
        assert second.fallback_count == 1
        assert round(second.action_success_percent, 2) == 66.67
        assert second.fallback_rate_percent == 50.0

    def test_record_intervention_increments_per_kind(self) -> None:
        session = ReplSession()

        session.record_intervention("ctrl_c")
        session.record_intervention("ctrl_c")
        session.record_intervention("correction")

        assert session.ctrl_c_intervention_count == 2
        assert session.correction_intervention_count == 1

    def test_record_intervention_kinds_are_independent(self) -> None:
        """Incrementing one kind does not touch the other."""
        session = ReplSession()

        session.record_intervention("correction")

        assert session.ctrl_c_intervention_count == 0
        assert session.correction_intervention_count == 1

    def test_fresh_session_starts_with_zero_intervention_counts(self) -> None:
        """A new ReplSession does not inherit any prior session's counters."""
        first = ReplSession()
        first.record_intervention("ctrl_c")
        first.record_intervention("correction")

        second = ReplSession()

        assert second.ctrl_c_intervention_count == 0
        assert second.correction_intervention_count == 0

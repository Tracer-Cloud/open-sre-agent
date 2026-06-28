"""Tests for ReplSession state."""

from __future__ import annotations

from pathlib import Path

import pytest

import config.constants as const_module
from interactive_shell.runtime.core.tasks import TaskRegistry
from interactive_shell.session import (
    SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
    ReplSession,
)
from interactive_shell.session.state import _scenario_id_from_synthetic_label
from platform.common.task_types import TaskKind


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
        assert session.pending_prompt_default is None
        assert session.last_synthetic_observation_path is None

    def test_take_pending_prompt_default_returns_and_clears(self) -> None:
        session = ReplSession()
        session.pending_prompt_default = "why did it fail?"
        assert session.take_pending_prompt_default() == "why did it fail?"
        assert session.pending_prompt_default is None
        assert session.take_pending_prompt_default() == ""

    def test_clear_resets_pending_prompt_default(self) -> None:
        session = ReplSession()
        session.pending_prompt_default = "why did it fail?"
        session.clear()
        assert session.pending_prompt_default is None

    def test_queue_auto_command_sets_pending_and_notifies(self) -> None:
        session = ReplSession()
        calls: list[bool] = []
        session.prompt_refresh_fn = lambda: calls.append(True)
        session.queue_auto_command("/integrations setup sentry")
        assert session.pending_prompt_default == "/integrations setup sentry"
        assert session.pending_prompt_autosubmit is True
        assert calls == [True]

    def test_take_pending_autosubmit_returns_and_clears(self) -> None:
        session = ReplSession()
        session.pending_prompt_autosubmit = True
        assert session.take_pending_autosubmit() is True
        assert session.pending_prompt_autosubmit is False
        assert session.take_pending_autosubmit() is False

    def test_clear_resets_pending_autosubmit(self) -> None:
        session = ReplSession()
        session.queue_auto_command("/integrations setup sentry")
        session.clear()
        assert session.pending_prompt_autosubmit is False
        assert session.pending_prompt_default is None

    def test_scenario_id_from_synthetic_label(self) -> None:
        assert (
            _scenario_id_from_synthetic_label(
                "opensre tests synthetic --scenario 001-replication-lag"
            )
            == "001-replication-lag"
        )
        assert _scenario_id_from_synthetic_label("rds_postgres:001-replication-lag") == (
            "001-replication-lag"
        )
        assert _scenario_id_from_synthetic_label("opensre tests synthetic --scenario ./evil") == ""
        assert _scenario_id_from_synthetic_label("rds_postgres:not-a-scenario") == ""

    def test_suggest_synthetic_failure_follow_up_sets_pending(self) -> None:
        session = ReplSession()
        session.suggest_synthetic_failure_follow_up(
            label="opensre tests synthetic --scenario 001-replication-lag",
        )
        assert session.pending_prompt_default == SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

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
        session.background_notification_preferences.set_channels(["email"])
        session.accumulated_context["service"] = "api"
        session.record("alert", "something")
        session.last_state = {"foo": "bar"}
        session.agent.messages.append(("user", "hey"))
        session.record_intervention("ctrl_c")
        session.record_intervention("correction")

        assert session.history_generation == 0
        session.clear()
        assert session.history_generation == 1

        assert session.history == []
        assert session.last_state is None
        assert session.accumulated_context == {}
        assert session.agent.messages == []
        assert session.task_registry.list_recent() == []
        assert session.ctrl_c_intervention_count == 0
        assert session.correction_intervention_count == 0
        assert session.background_notification_preferences.channels == ("email",)
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

"""Tests for per-kind message builders."""

from __future__ import annotations

import pytest

import platform.scheduler.tasks as tasks_mod
from platform.scheduler.loop_constants import LOOP_PROMPT_PARAM
from platform.scheduler.types import Provider, ScheduledTask, TaskKind


class TestMessageBuilders:
    def test_daily_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            window_hours=24,
        )
        # Mock the pipeline so the test doesn't need live infra
        monkeypatch.setattr(
            "tools.investigation.capability.run_investigation",
            lambda *_a, **_kw: {},
        )
        msg = tasks_mod.build_message(task)
        assert "Daily Reliability Summary" in msg
        assert "24h" in msg
        assert "OpenSRE" in msg

    def test_weekly_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            window_hours=168,
        )
        monkeypatch.setattr(
            "tools.investigation.capability.run_investigation",
            lambda *_a, **_kw: {},
        )
        msg = tasks_mod.build_message(task)
        assert "Weekly Alert Audit" in msg
        assert "168h" in msg

    def test_synthetic_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.SYNTHETIC_RUN,
            cron="0 6 * * *",
            provider=Provider.DISCORD,
            chat_id="123",
        )
        monkeypatch.setattr(
            "tools.investigation.capability.run_investigation",
            lambda *_a, **_kw: {},
        )
        msg = tasks_mod.build_message(task)
        assert "Synthetic Test Summary" in msg

    def test_daily_summary_uses_pipeline_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the pipeline returns a report, it is used as the message."""
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            window_hours=24,
        )
        monkeypatch.setattr(
            "tools.investigation.capability.run_investigation",
            lambda *_a, **_kw: {"report": "Real incident data from pipeline"},
        )
        msg = tasks_mod.build_message(task)
        assert msg == "Real incident data from pipeline"

    def test_weekly_audit_uses_pipeline_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            window_hours=168,
        )
        monkeypatch.setattr(
            "tools.investigation.capability.run_investigation",
            lambda *_a, **_kw: {"report": "Weekly audit from real data"},
        )
        msg = tasks_mod.build_message(task)
        assert msg == "Weekly audit from real data"

    def test_synthetic_run_uses_pipeline_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.SYNTHETIC_RUN,
            cron="0 6 * * *",
            provider=Provider.DISCORD,
            chat_id="123",
        )
        monkeypatch.setattr(
            "tools.investigation.capability.run_investigation",
            lambda *_a, **_kw: {"report": "3/3 probes passed"},
        )
        msg = tasks_mod.build_message(task)
        assert msg == "3/3 probes passed"

    def test_incident_window_replay_pipeline_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.INCIDENT_WINDOW_REPLAY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

        def _mock_build_replay(_t: ScheduledTask) -> str:
            raise RuntimeError("Pipeline failed")

        monkeypatch.setattr(tasks_mod, "_build_incident_window_replay", _mock_build_replay)

        with pytest.raises(RuntimeError, match="Pipeline failed"):
            tasks_mod._build_incident_window_replay(task)

    def test_custom_investigation_pipeline_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

        def _mock_build_custom(_t: ScheduledTask) -> str:
            raise RuntimeError("Custom investigation failed")

        monkeypatch.setattr(tasks_mod, "_build_custom_investigation", _mock_build_custom)

        with pytest.raises(RuntimeError, match="Custom investigation failed"):
            tasks_mod._build_custom_investigation(task)

    def test_daily_summary_pipeline_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            window_hours=24,
        )

        def _raise(_payload: object, **_kwargs: object) -> dict[str, str]:
            raise RuntimeError("pipeline down")

        monkeypatch.setattr("tools.investigation.capability.run_investigation", _raise)

        with pytest.raises(RuntimeError, match="Daily summary failed"):
            tasks_mod.build_message(task)

    def test_weekly_audit_pipeline_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            window_hours=168,
        )

        def _raise(_payload: object, **_kwargs: object) -> dict[str, str]:
            raise RuntimeError("pipeline down")

        monkeypatch.setattr("tools.investigation.capability.run_investigation", _raise)

        with pytest.raises(RuntimeError, match="Weekly audit failed"):
            tasks_mod.build_message(task)

    def test_custom_investigation_strips_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify credential keys are not passed to the investigation pipeline."""
        task = ScheduledTask(
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            params={"bot_token": "secret123", "custom_param": "safe_value"},
        )

        captured_payload: dict[str, object] = {}

        def _mock_run_investigation(payload: object, **_kwargs: object) -> dict[str, str]:
            captured_payload.update(payload)  # type: ignore[arg-type]
            return {"report": "test report"}

        monkeypatch.setattr(
            "tools.investigation.capability.run_investigation",
            _mock_run_investigation,
        )

        tasks_mod._build_custom_investigation(task)
        assert "bot_token" not in captured_payload
        assert captured_payload.get("custom_param") == "safe_value"

    def test_custom_investigation_with_loop_prompt_uses_agent_runner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Check incidents and summarize risk."},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Manual loop report"

        monkeypatch.setattr(
            "platform.scheduler.tasks.invoke_agent_runner",
            _mock_agent_runner,
        )
        monkeypatch.setattr(
            "platform.scheduler.tasks.invoke_investigation_runner",
            lambda _payload: pytest.fail("manual loops must not run RCA investigation"),
        )

        msg = tasks_mod._build_custom_investigation(task)

        assert msg == "Manual loop report"
        assert captured["source"] == "scheduled_manual_loop"
        assert captured["loop_prompt"] == "Check incidents and summarize risk."
        assert captured["name"] == "Morning ops"

    def test_sentry_morning_digest_uses_agent_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.SENTRY_MORNING_DIGEST,
            cron="0 8 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"project_slug": "api"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Top clusters: checkout failures"

        monkeypatch.setattr("platform.scheduler.tasks.invoke_agent_runner", _mock_agent_runner)
        msg = tasks_mod.build_message(task)
        assert msg == "Top clusters: checkout failures"
        assert captured["query"] == "is:unresolved"
        assert captured["stats_period"] == "24h"
        assert captured["project_slug"] == "api"

    def test_sentry_morning_digest_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.SENTRY_MORNING_DIGEST,
            cron="0 8 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

        def _raise(_payload: dict[str, object]) -> str:
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr("platform.scheduler.tasks.invoke_agent_runner", _raise)

        with pytest.raises(RuntimeError, match="Sentry morning digest failed"):
            tasks_mod.build_message(task)

    def test_sentry_uptime_watch_uses_agent_runner_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = ScheduledTask(
            id="uptime1",
            kind=TaskKind.SENTRY_UPTIME_WATCH,
            cron="*/5 * * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"project_slug": "api"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "CRITICAL downtime: api"

        monkeypatch.setattr("platform.scheduler.tasks.invoke_agent_runner", _mock_agent_runner)
        msg = tasks_mod.build_message(task)
        assert msg == "CRITICAL downtime: api"
        assert captured["source"] == "scheduled_sentry_uptime_watch"
        assert captured["task_id"] == "uptime1"
        assert captured["project_slug"] == "api"

    def test_posthog_metric_report_uses_agent_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            id="ph1",
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"stats_period": "30d", "metrics": "dau,signups"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Metric report: DAU up 12%"

        monkeypatch.setattr("platform.scheduler.tasks.invoke_agent_runner", _mock_agent_runner)
        msg = tasks_mod.build_message(task)
        assert msg == "Metric report: DAU up 12%"
        assert captured["source"] == "scheduled_posthog_metric_report"
        assert captured["task_id"] == "ph1"
        assert captured["stats_period"] == "30d"
        assert captured["metrics"] == "dau,signups"

    def test_posthog_metric_report_defaults_period(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "report"

        monkeypatch.setattr("platform.scheduler.tasks.invoke_agent_runner", _mock_agent_runner)
        tasks_mod.build_message(task)
        assert captured["stats_period"] == "7d"

    def test_posthog_metric_report_strips_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = ScheduledTask(
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"api_key": "secret", "stats_period": "7d"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "report"

        monkeypatch.setattr("platform.scheduler.tasks.invoke_agent_runner", _mock_agent_runner)
        tasks_mod.build_message(task)
        assert "api_key" not in captured
        assert captured["stats_period"] == "7d"

    def test_posthog_metric_report_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

        def _raise(_payload: dict[str, object]) -> str:
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr("platform.scheduler.tasks.invoke_agent_runner", _raise)

        with pytest.raises(RuntimeError, match="PostHog metric report failed"):
            tasks_mod.build_message(task)

"""Unit tests for github_pr_sweep task kind routing."""

from __future__ import annotations

from platform.scheduler.tasks import build_message
from platform.scheduler.types import Provider, ScheduledTask, TaskKind


def test_github_pr_sweep_kind_invokes_agent_runner(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_runner(payload: dict) -> str:
        calls.append(payload)
        return "PR sweep ok"

    monkeypatch.setattr(
        "platform.scheduler.tasks.invoke_agent_runner",
        fake_runner,
    )
    task = ScheduledTask(
        kind=TaskKind.GITHUB_PR_SWEEP,
        cron="0 9 * * 1-5",
        provider=Provider.SLACK,
        chat_id="C01234567",
    )
    assert build_message(task) == "PR sweep ok"
    assert calls[0]["source"] == "scheduled_github_pr_sweep"


def test_scheduled_agent_routes_github(monkeypatch) -> None:
    from integrations.scheduled_agent_bootstrap import run_scheduled_agent_digest

    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.run_github_pr_sweep",
        lambda _payload: "gh",
    )
    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.run_sentry_morning_digest",
        lambda _payload: "sentry",
    )
    assert run_scheduled_agent_digest({"source": "scheduled_github_pr_sweep"}) == "gh"
    assert run_scheduled_agent_digest({"source": "scheduled_sentry_morning_digest"}) == "sentry"

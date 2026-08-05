"""Tests for scheduled loop summaries and onboarding starter loops."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from platform.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
)
from platform.scheduler.loops import (
    STARTER_LOOPS,
    create_manual_loop,
    cron_for_time,
    delete_loop,
    list_loop_summaries,
    loop_name,
    normalize_loop_channels,
    parse_loop_time,
    seed_starter_loops,
    set_loop_enabled,
)
from platform.scheduler.store import add_task, list_tasks
from platform.scheduler.types import Provider, ScheduledTask, TaskKind


def test_seed_starter_loops_creates_draft_examples_once(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"

    first = seed_starter_loops(store_path)
    second = seed_starter_loops(store_path)

    assert len(first) == len(STARTER_LOOPS)
    assert second == []
    tasks = list_tasks(store_path)
    assert len(tasks) == len(STARTER_LOOPS)
    assert {task.name for task in tasks} >= {"Morning report", "Weekly alert audit", "PR sweep"}
    assert {task.enabled for task in tasks} == {False}


def test_loop_summaries_sort_active_first_and_compute_next_run(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    add_task(
        ScheduledTask(
            id="draft-loop",
            name="Draft",
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 9 * * 1",
            provider=Provider.SLACK,
            enabled=False,
        ),
        store_path,
    )
    add_task(
        ScheduledTask(
            id="active-loop",
            name="Active",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 8 * * 1-5",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        ),
        store_path,
    )

    loops = list_loop_summaries(
        store_path=store_path,
        now=datetime(2026, 8, 5, 7, 0, tzinfo=UTC),
    )

    assert [loop.id for loop in loops] == ["active-loop", "draft-loop"]
    assert loops[0].status == "active"
    assert loops[0].next_run == "2026-08-05T08:00:00+00:00"
    assert loops[1].status == "draft"


def test_loop_name_falls_back_to_kind_label() -> None:
    task = ScheduledTask(
        kind=TaskKind.GITHUB_PR_SWEEP,
        cron="0 9 * * 1-5",
        provider=Provider.SLACK,
    )

    assert loop_name(task) == "GitHub PR sweep"


def test_parse_loop_time_accepts_24_hour_and_meridiem_forms() -> None:
    assert parse_loop_time("08:30") == (8, 30)
    assert parse_loop_time("8am") == (8, 0)
    assert parse_loop_time("8:30pm") == (20, 30)
    assert parse_loop_time("12am") == (0, 0)
    assert parse_loop_time("12pm") == (12, 0)
    assert cron_for_time("08:30", weekdays=True) == "30 8 * * 1-5"


def test_parse_loop_time_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="hour"):
        parse_loop_time("25:00")
    with pytest.raises(ValueError, match="minute"):
        parse_loop_time("08:99")


def test_normalize_loop_channels_defaults_to_reachable_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_credentials",
        lambda _params: {"bot_token": "token"},
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_default_chat_id",
        lambda _params: "-100",
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_slack_credentials",
        lambda _params: {"webhook_url": "https://hooks.slack.test/services/T/B/x"},
    )

    assert normalize_loop_channels(None) == (
        Provider.TELEGRAM,
        Provider.SLACK,
        Provider.INTERACTIVE_SHELL,
    )


def test_create_manual_loop_persists_prompt_schedule_and_default_fanout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_credentials",
        lambda _params: {"bot_token": "token"},
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_default_chat_id",
        lambda _params: "-100",
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_slack_credentials",
        lambda _params: {"webhook_url": "https://hooks.slack.test/services/T/B/x"},
    )

    created = create_manual_loop(
        name="Morning ops",
        prompt="Check open incidents and summarize risk.",
        time_text="08:30",
        timezone="UTC",
        store_path=store_path,
    )

    task = list_tasks(store_path)[0]
    assert created.task.id == task.id
    assert created.channels == (
        Provider.TELEGRAM,
        Provider.SLACK,
        Provider.INTERACTIVE_SHELL,
    )
    assert task.name == "Morning ops"
    assert task.provider == Provider.INTERACTIVE_SHELL
    assert task.kind == TaskKind.CUSTOM_INVESTIGATION
    assert task.cron == "30 8 * * *"
    assert task.params[LOOP_PROMPT_PARAM] == "Check open incidents and summarize risk."
    assert task.params[LOOP_CHANNELS_PARAM] == "telegram,slack,interactive_shell"
    assert task.next_run is not None


def test_create_manual_loop_defaults_to_shell_when_no_external_handle_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_credentials",
        lambda _params: {},
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_default_chat_id",
        lambda _params: "",
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_slack_credentials",
        lambda _params: {},
    )

    created = create_manual_loop(
        name="Local loop",
        prompt="Check the local inbox.",
        time_text="9am",
        store_path=tmp_path / "scheduler_tasks.json",
    )

    assert created.channels == (Provider.INTERACTIVE_SHELL,)
    assert created.task.params[LOOP_CHANNELS_PARAM] == "interactive_shell"


def test_create_manual_loop_rejects_unready_explicit_telegram(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_credentials",
        lambda _params: {},
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_default_chat_id",
        lambda _params: "",
    )

    with pytest.raises(ValueError, match="telegram loop delivery needs"):
        create_manual_loop(
            name="Telegram loop",
            prompt="Send this to Telegram.",
            time_text="09:00",
            channels=["telegram"],
            store_path=tmp_path / "scheduler_tasks.json",
        )


def test_set_loop_enabled_stops_and_starts_grouped_tasks(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    add_task(
        ScheduledTask(
            id="loop-shell",
            name="Star loop",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="* * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={
                LOOP_GROUP_ID_PARAM: "star-loop",
                LOOP_PROMPT_PARAM: "Report stars",
            },
        ),
        store_path,
    )
    add_task(
        ScheduledTask(
            id="loop-telegram",
            name="Star loop",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="* * * * *",
            provider=Provider.TELEGRAM,
            params={
                LOOP_GROUP_ID_PARAM: "star-loop",
                LOOP_PROMPT_PARAM: "Report stars",
            },
        ),
        store_path,
    )

    stopped, error = set_loop_enabled("star-loop", enabled=False, store_path=store_path)

    assert error == ""
    assert stopped is not None
    assert stopped.task_count == 2
    assert {task.enabled for task in list_tasks(store_path)} == {False}

    started, error = set_loop_enabled("star-loop", enabled=True, store_path=store_path)

    assert error == ""
    assert started is not None
    assert started.task_count == 2
    assert {task.enabled for task in list_tasks(store_path)} == {True}
    assert all(task.next_run for task in list_tasks(store_path))


def test_delete_loop_removes_grouped_tasks(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    add_task(
        ScheduledTask(
            id="loop-shell",
            name="Star loop",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="* * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={
                LOOP_GROUP_ID_PARAM: "star-loop",
                LOOP_PROMPT_PARAM: "Report stars",
            },
        ),
        store_path,
    )
    add_task(
        ScheduledTask(
            id="other-loop",
            name="Other",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        ),
        store_path,
    )

    deleted, error = delete_loop("star-loop", store_path=store_path)

    assert error == ""
    assert deleted is not None
    assert deleted.task_ids == ("loop-shell",)
    assert [task.id for task in list_tasks(store_path)] == ["other-loop"]

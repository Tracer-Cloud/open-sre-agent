"""Tests for the interactive-shell prompt-loop scheduler lifecycle."""

from __future__ import annotations

import pytest

from platform.scheduler.loop_constants import LOOP_PROMPT_PARAM
from platform.scheduler.types import Provider, ScheduledTask, TaskKind
from surfaces.interactive_shell.runtime import loop_scheduler


class _FakeScheduler:
    def __init__(self) -> None:
        self.shutdown_waits: list[bool] = []

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_waits.append(wait)


@pytest.fixture(autouse=True)
def _reset_loop_scheduler() -> None:
    loop_scheduler.shutdown_loop_scheduler()
    yield
    loop_scheduler.shutdown_loop_scheduler()


def _manual_prompt_loop() -> ScheduledTask:
    return ScheduledTask(
        kind=TaskKind.CUSTOM_INVESTIGATION,
        cron="* * * * *",
        provider=Provider.INTERACTIVE_SHELL,
        params={LOOP_PROMPT_PARAM: "Report stars"},
    )


def _daily_digest() -> ScheduledTask:
    return ScheduledTask(
        kind=TaskKind.DAILY_SUMMARY,
        cron="0 9 * * *",
        provider=Provider.TELEGRAM,
    )


def test_start_loop_scheduler_installs_prompt_loop_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starts: list[object] = []

    def _install_runtime() -> None:
        starts.append("install")

    def _start_background_scheduler(*, task_filter=None):
        starts.append(task_filter)
        assert task_filter is not None
        assert task_filter(_manual_prompt_loop())
        assert not task_filter(_daily_digest())
        return _FakeScheduler(), 1

    monkeypatch.setattr(loop_scheduler, "install_runtime", _install_runtime)
    monkeypatch.setattr(
        loop_scheduler,
        "start_background_scheduler",
        _start_background_scheduler,
    )

    assert loop_scheduler.start_loop_scheduler() == 1
    assert loop_scheduler.start_loop_scheduler() == 1
    assert len(starts) == 2


def test_reload_loop_scheduler_replaces_existing_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedulers = [_FakeScheduler(), _FakeScheduler()]
    started: list[_FakeScheduler] = []
    reloads: list[str] = []

    def _install_runtime() -> None:
        return None

    def _start_background_scheduler(*, task_filter=None):
        _ = task_filter
        scheduler = schedulers.pop(0)
        started.append(scheduler)
        return scheduler, 1

    def _request_scheduler_reload() -> None:
        reloads.append("requested")

    monkeypatch.setattr(loop_scheduler, "install_runtime", _install_runtime)
    monkeypatch.setattr(
        loop_scheduler,
        "start_background_scheduler",
        _start_background_scheduler,
    )
    monkeypatch.setattr(
        "platform.scheduler.reload_signal.request_scheduler_reload",
        _request_scheduler_reload,
    )

    assert loop_scheduler.start_loop_scheduler() == 1
    assert started[0].shutdown_waits == []
    assert loop_scheduler.reload_loop_scheduler() == 1
    assert reloads == ["requested"]
    assert started[0].shutdown_waits == [False]
    assert started[1].shutdown_waits == []

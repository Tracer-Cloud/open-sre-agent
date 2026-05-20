"""Tests for the watchdog foreground loop."""

from __future__ import annotations

import pytest

from app.cli.support.errors import OpenSREError
from app.cli.support.exit_codes import ERROR, SUCCESS
from app.watch_dog.config import WatchdogConfig
from app.watch_dog.process_monitor import ProcessSample
from app.watch_dog.runner import run_watchdog


class _FakeSampler:
    def __init__(self, samples: list[ProcessSample]) -> None:
        self.samples = samples

    def sample(self) -> ProcessSample:
        if not self.samples:
            raise AssertionError("sample called too many times")
        return self.samples.pop(0)


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, threshold_name: str, message: str) -> bool:
        self.calls.append((threshold_name, message))
        return True


class _ExplodingSampler:
    def sample(self) -> ProcessSample:
        raise AssertionError("watchdog sampled before credentials were validated")


def _sample(
    *,
    cpu: float = 0.0,
    rss: int = 1024,
    runtime: float = 10.0,
    alive: bool = True,
) -> ProcessSample:
    return ProcessSample(
        pid=123,
        name="python",
        cmdline=("python", "worker.py"),
        cpu_percent=cpu,
        rss_bytes=rss,
        runtime_seconds=runtime,
        alive=alive,
        started_at=1_700_000_000.0,
    )


def test_once_exits_after_first_threshold_trip() -> None:
    dispatcher = _FakeDispatcher()
    code = run_watchdog(
        WatchdogConfig(pid=123, max_cpu=90, once=True),
        sampler=_FakeSampler([_sample(cpu=95)]),
        dispatcher=dispatcher,
        _sleep=lambda _seconds: None,
        _clock=lambda: 100.0,
    )

    assert code == ERROR
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0][0] == "max_cpu"
    assert "OpenSRE Watchdog Alarm" in dispatcher.calls[0][1]


def test_default_mode_keeps_polling_until_target_exits() -> None:
    dispatcher = _FakeDispatcher()
    sleeps: list[float] = []

    code = run_watchdog(
        WatchdogConfig.model_validate({"pid": 123, "max_runtime": "30s", "interval": 2}),
        sampler=_FakeSampler(
            [
                _sample(runtime=10),
                _sample(runtime=31),
                _sample(alive=False),
            ]
        ),
        dispatcher=dispatcher,
        _sleep=sleeps.append,
        _clock=lambda: 100.0,
    )

    assert code == SUCCESS
    assert sleeps == [2.0, 2.0]
    assert [name for name, _message in dispatcher.calls] == ["max_runtime"]


def test_target_exit_before_alarm_does_not_dispatch() -> None:
    dispatcher = _FakeDispatcher()

    code = run_watchdog(
        WatchdogConfig(pid=123, max_cpu=90),
        sampler=_FakeSampler([_sample(alive=False)]),
        dispatcher=dispatcher,
        _sleep=lambda _seconds: None,
        _clock=lambda: 100.0,
    )

    assert code == SUCCESS
    assert dispatcher.calls == []


def test_missing_credentials_fail_fast_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_missing_credentials(*, chat_id_override: str | None = None) -> object:
        raise OpenSREError("missing telegram credentials")

    monkeypatch.setattr(
        "app.watch_dog.runner.load_credentials_from_env",
        _raise_missing_credentials,
    )

    with pytest.raises(OpenSREError, match="missing telegram credentials"):
        run_watchdog(
            WatchdogConfig(pid=123, max_cpu=90),
            sampler=_ExplodingSampler(),
            _sleep=lambda _seconds: None,
            _clock=lambda: 100.0,
        )


def test_rss_threshold_formats_alarm_message() -> None:
    dispatcher = _FakeDispatcher()

    code = run_watchdog(
        WatchdogConfig.model_validate({"pid": 123, "max_rss": "4G", "once": True}),
        sampler=_FakeSampler([_sample(rss=5 * 1024**3)]),
        dispatcher=dispatcher,
        _sleep=lambda _seconds: None,
        _clock=lambda: 100.0,
    )

    assert code == ERROR
    assert "max_rss" in dispatcher.calls[0][1]
    assert "5.0GiB" in dispatcher.calls[0][1]


def test_alarm_message_uses_html_formatting() -> None:
    dispatcher = _FakeDispatcher()

    sample = ProcessSample(
        pid=123,
        name="python<script>",
        cmdline=("python", "worker.py", "--arg=<unsafe>"),
        cpu_percent=95.0,
        rss_bytes=1024,
        runtime_seconds=30.0,
        alive=True,
        started_at=1_700_000_000.0,
    )
    code = run_watchdog(
        WatchdogConfig(pid=123, max_cpu=90, once=True),
        sampler=_FakeSampler([sample]),
        dispatcher=dispatcher,
        _sleep=lambda _seconds: None,
        _clock=lambda: 100.0,
    )

    assert code == ERROR
    message = dispatcher.calls[0][1]
    assert "<b>🚨 OpenSRE Watchdog Alarm</b>" in message
    assert "&lt;unsafe&gt;" in message
    assert "<script>" not in message

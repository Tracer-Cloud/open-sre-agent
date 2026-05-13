"""Tests for watchdog configuration parsing and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.watch_dog.config import (
    WatchdogConfig,
    parse_byte_size,
    parse_duration_seconds,
)


def test_parse_duration_seconds_accepts_suffixes() -> None:
    assert parse_duration_seconds("30m") == 1800.0
    assert parse_duration_seconds("2h") == 7200.0
    assert parse_duration_seconds("15s") == 15.0
    assert parse_duration_seconds(5) == 5.0


def test_parse_byte_size_accepts_binary_suffixes() -> None:
    assert parse_byte_size("4G") == 4 * 1024**3
    assert parse_byte_size("512M") == 512 * 1024**2
    assert parse_byte_size("1K") == 1024
    assert parse_byte_size(4096) == 4096


def test_watchdog_config_requires_pid_or_name_xor() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        WatchdogConfig(pid=123, name="python", max_cpu=90)

    with pytest.raises(ValidationError, match="exactly one"):
        WatchdogConfig(max_cpu=90)


def test_watchdog_config_requires_at_least_one_threshold() -> None:
    with pytest.raises(ValidationError, match="at least one threshold"):
        WatchdogConfig(pid=123)


def test_watchdog_config_parses_runtime_rss_and_cooldown() -> None:
    config = WatchdogConfig(
        name="claude",
        max_runtime="30m",
        max_rss="4G",
        cooldown="5m",
        cpu_window="45s",
    )

    assert config.max_runtime == 1800.0
    assert config.max_rss == 4 * 1024**3
    assert config.cooldown == 300.0
    assert config.cpu_window == 45.0


def test_thresholds_are_returned_in_stable_order() -> None:
    config = WatchdogConfig(
        pid=123,
        max_rss="1G",
        max_cpu=90,
        max_runtime="2h",
    )

    assert [threshold.name for threshold in config.thresholds()] == [
        "max_cpu",
        "max_runtime",
        "max_rss",
    ]

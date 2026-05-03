from __future__ import annotations

from unittest.mock import patch

from app.remote.server import _check_memory_health


def test_check_memory_health_missing_meminfo_path() -> None:
    with patch("app.remote.server.Path.exists", return_value=False):
        check = _check_memory_health()
    assert check.status == "missing"
    assert "/proc/meminfo" in check.detail


def test_check_memory_health_missing_memavailable() -> None:
    meminfo = "\n".join(
        [
            "MemTotal:       1024000 kB",
            "MemFree:         123456 kB",
        ]
    )
    with (
        patch("app.remote.server.Path.exists", return_value=True),
        patch("app.remote.server.Path.read_text", return_value=meminfo),
    ):
        check = _check_memory_health()

    assert check.status == "missing"
    assert "Incomplete" in check.detail


def test_check_memory_health_read_error_is_missing() -> None:
    with (
        patch("app.remote.server.Path.exists", return_value=True),
        patch("app.remote.server.Path.read_text", side_effect=OSError("boom")),
    ):
        check = _check_memory_health()

    assert check.status == "missing"
    assert "Unable to read meminfo" in check.detail


def test_check_memory_health_happy_path_passed() -> None:
    meminfo = "\n".join(
        [
            "MemTotal:       1024000 kB",
            "MemAvailable:    900000 kB",
        ]
    )
    with (
        patch("app.remote.server.Path.exists", return_value=True),
        patch("app.remote.server.Path.read_text", return_value=meminfo),
    ):
        check = _check_memory_health()

    assert check.status == "passed"
    assert "% used" in check.detail


def test_check_memory_health_warn_threshold() -> None:
    # used_pct = ((1000-50)/1000)*100 = 95 -> warn
    meminfo = "\n".join(
        [
            "MemTotal:          1000 kB",
            "MemAvailable:        50 kB",
        ]
    )
    with (
        patch("app.remote.server.Path.exists", return_value=True),
        patch("app.remote.server.Path.read_text", return_value=meminfo),
    ):
        check = _check_memory_health()

    assert check.status == "warn"

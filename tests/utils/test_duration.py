"""Tests for the shared duration-formatting utility."""

import pytest

from platform.common.duration import format_duration


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (1, "1s"),
        (59, "59s"),
        (60, "1m 00s"),
        (65, "1m 05s"),
        (254, "4m 14s"),
        (3599, "59m 59s"),
        (3600, "60m 00s"),
    ],
)
def test_format_duration(seconds: int, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_format_duration_never_returns_a_bare_seconds_count_past_a_minute() -> None:
    """The reported defect: a report showed "Timing: 254s" instead of "4m 14s"."""
    assert format_duration(254) == "4m 14s"
    assert "254s" not in format_duration(254)


def test_format_duration_rejects_negative_input_by_clamping_to_zero() -> None:
    assert format_duration(-5) == "0s"


def test_format_duration_truncates_fractional_seconds() -> None:
    assert format_duration(254.9) == "4m 14s"

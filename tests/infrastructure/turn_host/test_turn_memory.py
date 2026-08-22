"""Resident-memory helpers used to size turn concurrency."""

from __future__ import annotations

from infrastructure.turn_host.turn_memory import current_rss_bytes, peak_rss_bytes


def test_current_rss_bytes_is_a_positive_measurement_or_none() -> None:
    # Contract: a real byte count where the platform exposes it, else None —
    # never zero, negative, or a raised error the turn host would have to catch.
    rss = current_rss_bytes()
    assert rss is None or rss > 0


def test_peak_rss_bytes_is_a_positive_measurement_or_none() -> None:
    peak = peak_rss_bytes()
    assert peak is None or peak > 0

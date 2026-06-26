from datetime import UTC, datetime

import pytest

from tools.fleet_monitoring.probe import ProcessSnapshot
from tools.fleet_monitoring.status import Status, compute_status


@pytest.mark.parametrize(
    "record, now, last_output, idle_after, stuck_after, expectation",
    [
        # High CPU + recent activity → active
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=23.5,
                rss_mb=128.0,
                num_fds=42,
                num_connections=3,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 3, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 2, 0, tzinfo=UTC),  # last_output
            120,
            480,
            Status.ACTIVE,
        ),
        # Low CPU + recent activity → active
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=4.8,
                rss_mb=83.0,
                num_fds=12,
                num_connections=1,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 3, 15, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 2, 15, tzinfo=UTC),  # last_output
            120,
            480,
            Status.ACTIVE,
        ),
        # Low CPU + silence >= idle_after_s → idle
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=4.8,
                rss_mb=83.0,
                num_fds=12,
                num_connections=1,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 7, 25, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 5, 25, tzinfo=UTC),  # last_output
            120,
            480,
            Status.IDLE,
        ),
        # High CPU + silence >= stuck_after_s → stuck
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=20.0,
                rss_mb=120,
                num_fds=32,
                num_connections=2,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 25, 25, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 5, 25, tzinfo=UTC),  # last_output
            120,
            480,
            Status.STUCK,
        ),
        # Edge: zero uptime
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=4.5,
                rss_mb=120,
                num_fds=32,
                num_connections=2,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # now
            None,  # last_output
            120,
            480,
            Status.ACTIVE,
        ),
        # Edge: exactly at threshold (exactly 120s)
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=4.5,
                rss_mb=120,
                num_fds=32,
                num_connections=2,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 7, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC),  # last_output
            120,
            480,
            Status.IDLE,
        ),
        # Edge: exactly at threshold (exactly 480s)
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=20.0,
                rss_mb=120,
                num_fds=32,
                num_connections=2,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 9, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 1, 0, tzinfo=UTC),  # last_output
            120,
            480,
            Status.STUCK,
        ),
        # Edge: last_output_at is not provided (None)
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=20.0,
                rss_mb=120,
                num_fds=32,
                num_connections=2,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 9, 0, tzinfo=UTC),  # now
            None,  # last_output
            120,
            480,
            Status.STUCK,
        ),
        # Edge: high CPU + silence between idle and stuck thresholds
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=30.0,
                rss_mb=250,
                num_fds=80,
                num_connections=5,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 14, 12, 7, 30, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 1, 0, tzinfo=UTC),  # last_output
            120,
            480,
            Status.ACTIVE,
        ),
    ],
)
def test_compute_status(record, now, last_output, idle_after, stuck_after, expectation) -> None:
    """Table-driven cases covering all three status classifications and boundary conditions."""
    assert (
        compute_status(
            record,
            now,
            last_output_at=last_output,
            idle_after_s=idle_after,
            stuck_after_s=stuck_after,
        )
        == expectation
    )


@pytest.mark.parametrize(
    "record, now, last_output, idle_after, stuck_after",
    [
        # now without timezone info
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=23.5,
                rss_mb=128.0,
                num_fds=42,
                num_connections=3,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 17, 12, 3, 0),  # now
            datetime(2026, 5, 17, 12, 2, 0, tzinfo=UTC),  # last_output
            120,
            480,
        ),
        # last_output_at without timezone info
        (
            ProcessSnapshot(
                pid=8421,
                cpu_percent=4.8,
                rss_mb=83.0,
                num_fds=12,
                num_connections=1,
                status="running",
                started_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
            ),
            datetime(2026, 5, 17, 12, 3, 15, tzinfo=UTC),  # now
            datetime(2026, 5, 17, 12, 2, 15),  # last_output
            120,
            480,
        ),
    ],
)
def test_compute_status_raises_on_naive_datetime(record, now, last_output, idle_after, stuck_after):
    with pytest.raises(ValueError, match="naive datetime"):
        compute_status(
            record,
            now,
            last_output_at=last_output,
            idle_after_s=idle_after,
            stuck_after_s=stuck_after,
        )

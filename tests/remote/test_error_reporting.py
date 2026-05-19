from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.remote.error_reporting import report_remote_exception


def test_report_remote_exception_adds_remote_tags() -> None:
    logger = MagicMock(spec=logging.Logger)
    exc = RuntimeError("boom")

    with patch("app.remote.error_reporting.report_exception") as report:
        report_remote_exception(
            exc,
            logger=logger,
            component="client",
            event="preflight_failed",
            message="failed",
            severity="warning",
            tags={"candidate_id": "dpl_123"},
            extras={"base_url": "http://host:2024"},
        )

    report.assert_called_once_with(
        exc,
        logger=logger,
        message="failed",
        severity="warning",
        tags={
            "surface": "remote_server",
            "component": "client",
            "event": "preflight_failed",
            "candidate_id": "dpl_123",
        },
        extras={"base_url": "http://host:2024"},
    )

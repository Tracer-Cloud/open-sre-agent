"""Programmatic SDK entry point — public API for running investigations."""

from __future__ import annotations

from typing import Any

from app.analytics.cli import track_investigation
from app.analytics.source import EntrypointSource, TriggerMode


def run_investigation(*args: Any, **kwargs: Any) -> Any:
    """Lazily import the full runner stack to avoid optional dependency churn at import time."""
    from app.pipeline.runners import run_investigation as _run_investigation

    with track_investigation(
        entrypoint=EntrypointSource.SDK,
        trigger_mode=TriggerMode.SERVICE_RUNTIME,
    ):
        return _run_investigation(*args, **kwargs)

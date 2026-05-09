"""Watchdog package."""

from __future__ import annotations

from app.watch_dog.alarms import (
    AlarmCredentials,
    AlarmDispatcher,
    load_credentials_from_env,
)

__all__ = [
    "AlarmCredentials",
    "AlarmDispatcher",
    "load_credentials_from_env",
]

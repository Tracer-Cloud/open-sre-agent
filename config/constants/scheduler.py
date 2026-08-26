"""Scheduler-process environment variable names."""

from __future__ import annotations

# Whether the gateway process co-hosts the scheduler loop in-process. On by
# default (single-process deployment). Set false to run the scheduler as its own
# service (MODE=scheduler / `opensre cron start`) so tasks are not fired twice.
OPENSRE_GATEWAY_HOST_SCHEDULER_ENV = "OPENSRE_GATEWAY_HOST_SCHEDULER"

__all__ = ["OPENSRE_GATEWAY_HOST_SCHEDULER_ENV"]

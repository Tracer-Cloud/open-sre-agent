"""Scheduled-delivery adapter: persist a scheduled task's message to the shell inbox.

Local delivery (no vendor), so this adapter lives in ``infrastructure`` rather
than an ``integrations`` package.
"""

from __future__ import annotations

import logging

from infrastructure.scheduling.scheduler.delivery import strip_html
from infrastructure.scheduling.scheduler.local_delivery import record_loop_message
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)


class InteractiveShellScheduledDelivery:
    """Persist a scheduled task's loop output to the local interactive-shell inbox."""

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        try:
            message_id = record_loop_message(task, strip_html(message))
            return True, "", message_id
        except Exception as exc:
            logger.warning("Interactive-shell loop delivery failed for task %s: %s", task.id, exc)
            return False, f"Interactive-shell delivery error: {type(exc).__name__}", ""

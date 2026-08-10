"""Gateway implementation of investigation launch ports.

Provides headless investigation launching capabilities for the gateway
by delegating to the detached investigation system.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from rich.console import Console

from platform.common.task_types import TaskKind, TaskRecord, TaskStatus
from tools.interactive_shell.shared.execution_policy import ExecutionPolicyResult
from tools.interactive_shell.shared.investigation_launch import (
    ForegroundInvestigationResult,
    ForegroundInvestigationStatus,
    InvestigationLaunchPorts,
    InvestigationSession,
)

from .detached_launcher import launch_detached_investigation

logger = logging.getLogger(__name__)


class GatewayInvestigationLaunchPorts:
    """Gateway implementation of investigation launch ports."""

    def execution_allowed(
        self,
        *,
        policy: ExecutionPolicyResult,  # noqa: ARG002
        session: InvestigationSession,  # noqa: ARG002
        console: Console,  # noqa: ARG002
        action_summary: str,  # noqa: ARG002
        confirm_fn: Any | None,  # noqa: ARG002
        is_tty: bool | None,  # noqa: ARG002
        action_already_listed: bool,  # noqa: ARG002
    ) -> bool:
        """Always allow execution in gateway context."""
        return True

    def background_mode_enabled(self, session: InvestigationSession) -> bool:  # noqa: ARG002
        """Background mode is never enabled in gateway context."""
        return False

    def run_text_investigation(
        self,
        *,
        alert_text: str,
        context_overrides: dict[str, Any] | None,
        cancel_requested: Any,  # noqa: ARG002
        console: Console,  # noqa: ARG002
    ) -> dict[str, object]:
        """Run a text investigation via detached launcher."""
        result = launch_detached_investigation(
            alert_text=alert_text,
            context_overrides=context_overrides,
        )
        return {
            "investigation_id": result.investigation_id,
            "status": "queued" if result.accepted else "refused",
            "message": result.refusal_reason if not result.accepted else "Investigation started",
        }

    def run_sample_alert(
        self,
        *,
        template_name: str,
        context_overrides: dict[str, Any] | None,
        cancel_requested: Any,  # noqa: ARG002
        console: Console,  # noqa: ARG002
    ) -> dict[str, object]:
        """Run a sample alert investigation via detached launcher."""
        result = launch_detached_investigation(
            alert_text=f"Sample alert from {template_name}",
            context_overrides=context_overrides,
        )
        return {
            "investigation_id": result.investigation_id,
            "status": "queued" if result.accepted else "refused",
            "message": result.refusal_reason
            if not result.accepted
            else "Sample investigation started",
        }

    def start_background_text(
        self,
        *,
        alert_text: str,  # noqa: ARG002
        session: InvestigationSession,  # noqa: ARG002
        console: Console,  # noqa: ARG002
        display_command: str,  # noqa: ARG002
    ) -> None:
        """Log and return - background mode is disabled in gateway."""
        logger.warning("start_background_text called in gateway context - background mode disabled")

    def start_background_sample(
        self,
        *,
        template_name: str,  # noqa: ARG002
        session: InvestigationSession,  # noqa: ARG002
        console: Console,  # noqa: ARG002
        display_command: str,  # noqa: ARG002
    ) -> None:
        """Log and return - background mode is disabled in gateway."""
        logger.warning(
            "start_background_sample called in gateway context - background mode disabled"
        )

    def run_foreground_investigation(
        self,
        *,
        session: InvestigationSession,  # noqa: ARG002
        console: Console,  # noqa: ARG002
        task_command: str,
        run: Callable[[TaskRecord], dict[str, object]],
        exception_context: str,  # noqa: ARG002
        target: str,  # noqa: ARG002
    ) -> ForegroundInvestigationResult:
        """Run a foreground investigation by invoking the provided run closure."""
        task = TaskRecord(
            task_id=uuid4().hex,
            kind=TaskKind.INVESTIGATION,
            status=TaskStatus.RUNNING,
            command=task_command,
        )

        try:
            result = run(task)
        except Exception:
            logger.exception("Investigation run failed")
            return ForegroundInvestigationResult(status=ForegroundInvestigationStatus.FAILED)

        status_value = result.get("status")
        if status_value in ("refused", "failed", "error"):
            return ForegroundInvestigationResult(status=ForegroundInvestigationStatus.FAILED)
        return ForegroundInvestigationResult(status=ForegroundInvestigationStatus.COMPLETED)


def gateway_investigation_launch_ports() -> InvestigationLaunchPorts:
    """Factory for gateway investigation launch ports."""
    return GatewayInvestigationLaunchPorts()

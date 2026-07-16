"""Shared launch flow for investigation-style tools.

``investigation_start`` (free-text) and ``alert_sample`` (template) share the
same shape: gate through the execution policy, announce, run in the background or
foreground, and record the outcome. This helper holds that flow once; each tool
supplies only the parts that differ (the run callable, the background launcher,
and the display/record strings).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from rich.console import Console
from rich.markup import escape

from platform.common.task_types import TaskRecord
from tools.interactive_shell.shared.execution_policy import (
    ExecutionPolicyResult,
    plan_foreground_tool,
)

ForegroundInvestigationStatus = Literal["completed", "failed", "cancelled"]


class InvestigationSession(Protocol):
    accumulated_context: dict[str, Any]

    def record(
        self,
        kind: str,
        value: str,
        *,
        ok: bool = True,
        **metadata: Any,
    ) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class ForegroundInvestigationResult:
    """Minimal foreground investigation outcome for launch gating."""

    status: ForegroundInvestigationStatus


@runtime_checkable
class InvestigationLaunchPorts(Protocol):
    """Surface-specific hooks for gating and foreground investigation UX."""

    def execution_allowed(
        self,
        *,
        policy: ExecutionPolicyResult,
        session: InvestigationSession,
        console: Console,
        action_summary: str,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        action_already_listed: bool,
    ) -> bool:
        raise NotImplementedError

    def background_mode_enabled(
        self,
        session: InvestigationSession,
    ) -> bool:
        raise NotImplementedError

    def run_text_investigation(
        self,
        *,
        alert_text: str,
        context_overrides: dict[str, Any] | None,
        cancel_requested: Any,
    ) -> dict[str, object]:
        raise NotImplementedError

    def run_sample_alert(
        self,
        *,
        template_name: str,
        context_overrides: dict[str, Any] | None,
        cancel_requested: Any,
    ) -> dict[str, object]:
        raise NotImplementedError

    def start_background_text(
        self,
        *,
        alert_text: str,
        session: InvestigationSession,
        console: Console,
        display_command: str,
    ) -> None:
        raise NotImplementedError

    def start_background_sample(
        self,
        *,
        template_name: str,
        session: InvestigationSession,
        console: Console,
        display_command: str,
    ) -> None:
        raise NotImplementedError

    def run_foreground_investigation(
        self,
        *,
        session: InvestigationSession,
        console: Console,
        task_command: str,
        run: Callable[[TaskRecord], dict[str, object]],
        exception_context: str,
        target: str,
    ) -> ForegroundInvestigationResult:
        raise NotImplementedError


def launch_investigation(
    *,
    session: InvestigationSession,
    console: Console,
    ports: InvestigationLaunchPorts,
    tool_type: str,
    action_summary: str,
    announce_label: str,
    announce_value: str,
    record_value: str,
    foreground_task_command: str,
    exception_context: str,
    run: Callable[[TaskRecord], dict[str, object]],
    start_background: Callable[[], None],
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    """Gate, announce, and run an investigation-style tool, recording the outcome.

    Every outcome is recorded on the ``alert`` channel keyed by ``record_value``.
    """
    plan = plan_foreground_tool(tool_type, "investigation_launch")
    if not ports.execution_allowed(
        policy=plan.policy,
        session=session,
        console=console,
        action_summary=action_summary,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("alert", record_value, ok=False)
        return

    console.print(f"[bold]{announce_label}:[/bold] {escape(announce_value)}")

    if ports.background_mode_enabled(session):
        start_background()
        session.record("alert", record_value)
        return

    if (
        ports.run_foreground_investigation(
            session=session,
            console=console,
            task_command=foreground_task_command,
            run=run,
            exception_context=exception_context,
            target=record_value,
        ).status
        != "completed"
    ):
        session.record("alert", record_value, ok=False)
        return

    session.record("alert", record_value)


__all__ = [
    "ForegroundInvestigationResult",
    "InvestigationLaunchPorts",
    "InvestigationSession",
    "launch_investigation",
]

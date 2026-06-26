"""Investigation and sample-alert runner."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    execution_allowed,
    plan_investigation_execution,
)
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.runtime.foreground_investigation import run_foreground_investigation
from cli.interactive_shell.runtime.tasks import TaskRecord


def run_sample_alert(
    template_name: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    from cli.investigation import run_sample_alert_for_session

    plan = plan_investigation_execution(action_type="sample_alert", user_initiated=True)
    if not execution_allowed(
        plan.policy,
        session=session,
        console=console,
        action_summary=f"sample alert investigation ({template_name})",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("alert", f"sample:{template_name}", ok=False)
        return

    console.print(f"[bold]sample alert:[/bold] {escape(template_name)}")
    if session.background_mode_enabled:
        from cli.interactive_shell.runtime.background_runner import (
            start_background_template_investigation,
        )

        start_background_template_investigation(
            template_name=template_name,
            session=session,
            console=console,
            display_command=f"sample alert:{template_name}",
        )
        session.record("alert", f"sample:{template_name}")
        return

    def _run(task: TaskRecord) -> dict[str, object]:
        return run_sample_alert_for_session(
            template_name=template_name,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )

    if (
        run_foreground_investigation(
            session=session,
            console=console,
            task_command=f"sample alert:{template_name}",
            run=_run,
            exception_context="interactive_shell.sample_alert",
        )
        is None
    ):
        session.record("alert", f"sample:{template_name}", ok=False)
        return

    session.record("alert", f"sample:{template_name}")


def run_text_investigation(
    alert_text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    from cli.investigation import run_investigation_for_session

    plan = plan_investigation_execution(action_type="investigation", user_initiated=True)
    if not execution_allowed(
        plan.policy,
        session=session,
        console=console,
        action_summary=f'investigation from text "{alert_text}"',
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("alert", alert_text, ok=False)
        return

    console.print(f"[bold]investigation:[/bold] {escape(alert_text)}")
    if session.background_mode_enabled:
        from cli.interactive_shell.runtime.background_runner import (
            start_background_text_investigation,
        )

        start_background_text_investigation(
            alert_text=alert_text,
            session=session,
            console=console,
            display_command="background free-text investigation",
        )
        session.record("alert", alert_text)
        return

    def _run(task: TaskRecord) -> dict[str, object]:
        return run_investigation_for_session(
            alert_text=alert_text,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )

    if (
        run_foreground_investigation(
            session=session,
            console=console,
            task_command=f"investigate:{alert_text}",
            run=_run,
            exception_context="interactive_shell.text_investigation",
        )
        is None
    ):
        session.record("alert", alert_text, ok=False)
        return

    session.record("alert", alert_text)

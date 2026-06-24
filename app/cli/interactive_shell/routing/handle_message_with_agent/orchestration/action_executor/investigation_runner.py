"""Investigation and sample-alert runner."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.error_handling.errors import OpenSREError
from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    execution_allowed,
    plan_investigation_execution,
)
from app.cli.interactive_shell.runtime import ReplSession, TaskKind
from app.cli.interactive_shell.ui import ERROR, WARNING


def run_sample_alert(
    template_name: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    from app.cli.investigation import run_sample_alert_for_session

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
        from app.cli.interactive_shell.runtime.background_runner import (
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

    task = session.task_registry.create(
        TaskKind.INVESTIGATION, command=f"sample alert:{template_name}"
    )
    task.mark_running()
    try:
        final_state = run_sample_alert_for_session(
            template_name=template_name,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.sample_alert")
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.apply_investigation_result(final_state)
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
    from app.cli.investigation import run_investigation_for_session

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
        from app.cli.interactive_shell.runtime.background_runner import (
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

    task = session.task_registry.create(TaskKind.INVESTIGATION, command=f"investigate:{alert_text}")
    task.mark_running()
    try:
        final_state = run_investigation_for_session(
            alert_text=alert_text,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        session.record("alert", alert_text, ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        session.record("alert", alert_text, ok=False)
        return
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.text_investigation")
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", alert_text, ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.apply_investigation_result(final_state)
    session.record("alert", alert_text)

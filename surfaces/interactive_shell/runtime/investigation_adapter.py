"""REPL adapters for session investigation streaming and action-tool launch."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any, Protocol, cast

from rich.console import Console

from core.agent_harness.session.terminal_access import background_mode_enabled
from core.domain.stream import StreamEvent
from platform.common.task_types import TaskRecord
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from surfaces.interactive_shell.ui.foreground_investigation import run_foreground_investigation
from tools.interactive_shell.shared.execution_policy import ExecutionPolicyResult
from tools.interactive_shell.shared.investigation_launch import (
    ForegroundInvestigationResult,
    InvestigationLaunchPorts,
    InvestigationSession,
)
from tools.investigation import session_runner


class BackgroundTextLauncher(Protocol):
    """Callable contract for starting a background text investigation."""

    def __call__(
        self,
        *,
        alert_text: str,
        session: Session,
        console: Console,
        display_command: str,
    ) -> str:
        raise NotImplementedError


class BackgroundSampleLauncher(Protocol):
    """Callable contract for starting a background sample investigation."""

    def __call__(
        self,
        *,
        template_name: str,
        session: Session,
        console: Console,
        display_command: str,
    ) -> str:
        raise NotImplementedError


def repl_foreground_renderer() -> session_runner.StreamRendererFn:
    """Return a renderer that streams investigation progress to the REPL terminal."""
    from surfaces.cli.ui.renderer import StreamRenderer

    def _render(events: Iterator[StreamEvent]) -> dict[str, Any]:
        renderer = StreamRenderer(local=True)
        return dict(renderer.render_stream(events))

    return _render


def repl_background_renderer() -> session_runner.StreamRendererFn:
    """Return a silent renderer for background investigations."""
    from surfaces.cli.ui.renderer import StreamRenderer
    from surfaces.interactive_shell.ui.output import reset_tracker, set_silent_tracker

    def _render(events: Iterator[StreamEvent]) -> dict[str, Any]:
        set_silent_tracker()
        try:
            renderer = StreamRenderer(local=True, display=False)
            return dict(renderer.render_stream(events))
        finally:
            reset_tracker()

    return _render


def run_investigation_for_session(
    *,
    alert_text: str,
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a foreground streaming investigation in the REPL."""
    return session_runner.run_investigation_for_session(
        alert_text=alert_text,
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=repl_foreground_renderer(),
    )


def run_sample_alert_for_session(
    *,
    template_name: str = "generic",
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a foreground sample-alert investigation in the REPL."""
    return session_runner.run_sample_alert_for_session(
        template_name=template_name,
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=repl_foreground_renderer(),
    )


def run_investigation_for_session_background(
    *,
    alert_text: str,
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a silent background investigation in the REPL."""
    return session_runner.run_investigation_for_session_background(
        alert_text=alert_text,
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=repl_background_renderer(),
    )


def run_sample_alert_for_session_background(
    *,
    template_name: str = "generic",
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a silent background sample-alert investigation in the REPL."""
    return session_runner.run_sample_alert_for_session_background(
        template_name=template_name,
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=repl_background_renderer(),
    )


class ReplInvestigationLaunchPorts:
    """Default REPL ports for investigation-style action tools."""

    def __init__(
        self,
        *,
        start_background_text: BackgroundTextLauncher,
        start_background_sample: BackgroundSampleLauncher,
    ) -> None:
        self._start_background_text = start_background_text
        self._start_background_sample = start_background_sample

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
        return execution_allowed(
            policy,
            session=cast(Session, session),
            console=console,
            action_summary=action_summary,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            action_already_listed=action_already_listed,
        )

    def background_mode_enabled(self, session: InvestigationSession) -> bool:
        return background_mode_enabled(cast(Session, session))

    def run_text_investigation(
        self,
        *,
        alert_text: str,
        context_overrides: dict[str, Any] | None,
        cancel_requested: Any,
    ) -> dict[str, object]:
        return run_investigation_for_session(
            alert_text=alert_text,
            context_overrides=context_overrides,
            cancel_requested=cancel_requested,
        )

    def run_sample_alert(
        self,
        *,
        template_name: str,
        context_overrides: dict[str, Any] | None,
        cancel_requested: Any,
    ) -> dict[str, object]:
        return run_sample_alert_for_session(
            template_name=template_name,
            context_overrides=context_overrides,
            cancel_requested=cancel_requested,
        )

    def start_background_text(
        self,
        *,
        alert_text: str,
        session: InvestigationSession,
        console: Console,
        display_command: str,
    ) -> None:
        self._start_background_text(
            alert_text=alert_text,
            session=cast(Session, session),
            console=console,
            display_command=display_command,
        )

    def start_background_sample(
        self,
        *,
        template_name: str,
        session: InvestigationSession,
        console: Console,
        display_command: str,
    ) -> None:
        self._start_background_sample(
            template_name=template_name,
            session=cast(Session, session),
            console=console,
            display_command=display_command,
        )

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
        outcome = run_foreground_investigation(
            session=cast(Session, session),
            console=console,
            task_command=task_command,
            run=run,
            exception_context=exception_context,
            target=target,
        )
        return ForegroundInvestigationResult(status=outcome.status)


def repl_investigation_launch_ports(
    *,
    start_background_text: BackgroundTextLauncher,
    start_background_sample: BackgroundSampleLauncher,
) -> InvestigationLaunchPorts:
    """Return REPL investigation launch ports for action tools."""
    return ReplInvestigationLaunchPorts(
        start_background_text=start_background_text,
        start_background_sample=start_background_sample,
    )


__all__ = [
    "ReplInvestigationLaunchPorts",
    "repl_background_renderer",
    "repl_foreground_renderer",
    "repl_investigation_launch_ports",
    "run_investigation_for_session",
    "run_investigation_for_session_background",
    "run_sample_alert_for_session",
    "run_sample_alert_for_session_background",
]

"""Surface-agnostic session investigation streaming orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any, NoReturn

from config.config import resolve_llm_settings
from core.domain.stream import StreamEvent
from platform.common.errors import OpenSREError
from tools.investigation.alert_templates import build_alert_template

_logger = logging.getLogger(__name__)

_SESSION_EVENT_POLL_S = 0.25

StreamRendererFn = Callable[[Iterator[StreamEvent]], dict[str, Any]]


class InvestigationPumpCancelled(Exception):
    """Propagated when the async pump task was cancelled (distinct from Ctrl+C SIGINT)."""


def check_llm_settings() -> None:
    """Validate LLM settings early and surface misconfiguration as a structured error."""
    from pydantic import ValidationError

    try:
        settings = resolve_llm_settings()
    except ValidationError as exc:
        errors = exc.errors()
        if errors:
            ctx = errors[0].get("ctx", {})
            original = ctx.get("error")
            msg = str(original) if isinstance(original, Exception) else errors[0]["msg"]
        else:
            msg = str(exc)
        raise OpenSREError(
            msg,
            suggestion="Run `opensre onboard` to configure your LLM provider and API credentials.",
        ) from exc

    provider = getattr(settings, "provider", None)
    if not isinstance(provider, str):
        return
    from config.llm_auth.credentials import status as credential_status

    auth_status = credential_status(provider)
    if auth_status.configured and not auth_status.stale:
        return
    state = "stale" if auth_status.stale else "missing"
    raise OpenSREError(
        f"LLM provider '{provider}' credentials are {state}: {auth_status.detail}",
        suggestion=(
            f"Run `opensre auth verify {provider}` or `opensre auth login {provider}` "
            "before starting an investigation."
        ),
    )


def reraise_investigation_failure(exc: BaseException) -> NoReturn:
    """Map investigation runtime failures to structured errors."""
    from core.llm_invoke_errors import classify_llm_invoke_failure

    if isinstance(exc, InvestigationPumpCancelled):
        raise OpenSREError(
            "Investigation streaming stopped before completion.",
            suggestion="The run was cancelled or closed early. Retry if you still need results.",
        ) from exc

    classified = classify_llm_invoke_failure(exc)
    if classified is not None:
        suggestion = (
            "\n".join(classified.remediation_steps) if classified.remediation_steps else None
        )
        raise OpenSREError(classified.user_message, suggestion=suggestion) from exc

    raise exc


def _alert_payload_with_context(
    raw_alert: dict[str, Any],
    context_overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    if not context_overrides:
        return raw_alert
    return {
        **raw_alert,
        "annotations": {
            **raw_alert.get("annotations", {}),
            **context_overrides,
        },
    }


def run_session_alert_payload(
    *,
    raw_alert: dict[str, Any],
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
    render_stream: StreamRendererFn,
) -> dict[str, Any]:
    """Run a streaming investigation from an already-structured session alert."""
    from tools.investigation.capability import astream_investigation

    check_llm_settings()
    alert_payload = _alert_payload_with_context(raw_alert, context_overrides)

    event_queue: queue.Queue[StreamEvent | BaseException | None] = queue.Queue()
    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    pump_task_ref: dict[str, asyncio.Task[None]] = {}

    def _run_async() -> None:
        loop = asyncio.new_event_loop()
        loop_ref["loop"] = loop
        try:

            async def _pump() -> None:
                async for evt in astream_investigation(
                    raw_alert=alert_payload,
                ):
                    event_queue.put(evt)

            task = loop.create_task(_pump())
            pump_task_ref["task"] = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                event_queue.put(InvestigationPumpCancelled())
        except Exception as exc:
            event_queue.put(exc)
        finally:
            event_queue.put(None)
            loop.close()

    # Copy the caller's context so ContextVar bindings (session trace) reach the thread.
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(_run_async,), daemon=True
    )
    thread.start()

    def _cancel_pump() -> None:
        loop = loop_ref.get("loop")
        task = pump_task_ref.get("task")
        if loop is None or task is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(task.cancel)

    def _events() -> Iterator[StreamEvent]:
        try:
            while True:
                if cancel_requested is not None and cancel_requested.is_set():
                    _cancel_pump()
                    raise KeyboardInterrupt
                try:
                    item = event_queue.get(timeout=_SESSION_EVENT_POLL_S)
                except queue.Empty:
                    continue
                if isinstance(item, BaseException):
                    from platform.analytics.investigation_loop import (
                        publish_loop_metrics_from_stream_failure,
                    )

                    thread.join(timeout=5)
                    reraise_investigation_failure(publish_loop_metrics_from_stream_failure(item))
                if item is None:
                    return
                yield item
        finally:
            _cancel_pump()

    try:
        rendered_state = render_stream(_events())
    except KeyboardInterrupt:
        _cancel_pump()
        raise
    finally:
        thread.join(timeout=5)
        if thread.is_alive():
            _logger.warning(
                "investigation thread did not terminate within 5s after cancellation; "
                "an LLM call may still be in flight"
            )
    return dict(rendered_state)


def run_investigation_for_session(
    *,
    alert_text: str,
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
    render_stream: StreamRendererFn,
) -> dict[str, Any]:
    """Run a streaming investigation from a free-text alert description."""
    raw_alert: dict[str, Any] = {"alert_name": "Interactive session", "message": alert_text}
    return run_session_alert_payload(
        raw_alert=raw_alert,
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=render_stream,
    )


def run_sample_alert_for_session(
    *,
    template_name: str = "generic",
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
    render_stream: StreamRendererFn,
) -> dict[str, Any]:
    """Run a streaming investigation for a built-in sample alert."""
    return run_session_alert_payload(
        raw_alert=build_alert_template(template_name),
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=render_stream,
    )


def run_investigation_for_session_background(
    *,
    alert_text: str,
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
    render_stream: StreamRendererFn,
) -> dict[str, Any]:
    """Run a non-rendering investigation for session-local background tasks."""
    raw_alert: dict[str, Any] = {"alert_name": "Interactive session", "message": alert_text}
    return run_session_alert_payload(
        raw_alert=raw_alert,
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=render_stream,
    )


def run_sample_alert_for_session_background(
    *,
    template_name: str = "generic",
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
    render_stream: StreamRendererFn,
) -> dict[str, Any]:
    """Run a non-rendering sample-alert investigation for background tasks."""
    return run_session_alert_payload(
        raw_alert=build_alert_template(template_name),
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
        render_stream=render_stream,
    )


__all__ = [
    "InvestigationPumpCancelled",
    "StreamRendererFn",
    "check_llm_settings",
    "reraise_investigation_failure",
    "run_investigation_for_session",
    "run_investigation_for_session_background",
    "run_sample_alert_for_session",
    "run_sample_alert_for_session_background",
    "run_session_alert_payload",
]

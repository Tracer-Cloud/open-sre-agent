"""Prompt redraw and pending-input prefill wiring."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from surfaces.interactive_shell.runtime import Session


def wire_prompt_refresh(
    session: Session,
    pt_app: Any,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[], None]:
    """Register session hook to prefill pending text and redraw the active prompt."""

    def invalidate_prompt() -> None:
        loop.call_soon_threadsafe(pt_app.invalidate)

    def refresh_active_prompt() -> None:
        def _apply() -> None:
            pending = session.pending_prompt_default
            buffer = pt_app.current_buffer
            # Never clobber text the user is actively typing.
            if not pending or buffer.text:
                invalidate_prompt()
                return
            if session.pending_prompt_autosubmit:
                # Auto-submit an agent-queued interactive command so it dispatches
                # through the normal exclusive-stdin path (the only place an
                # interactive child process gets clean stdin). Note: pt_app.is_running
                # under-reports while prompt_async awaits during a dispatch, so we do
                # not gate on it; validate_and_handle works regardless. If the app is
                # genuinely not accepting input, leave the prefill in place so the
                # next prompt iteration picks it up via the before-prompt path.
                session.pending_prompt_default = None
                session.take_pending_autosubmit()
                buffer.text = pending
                try:
                    buffer.validate_and_handle()
                except Exception:  # noqa: BLE001
                    session.pending_prompt_default = pending
                    session.pending_prompt_autosubmit = True
            elif pt_app.is_running:
                session.pending_prompt_default = None
                buffer.text = pending
            invalidate_prompt()

        loop.call_soon_threadsafe(_apply)

    session.prompt_refresh_fn = refresh_active_prompt
    return invalidate_prompt

"""Prompt-toolkit runtime loop for interactive shell."""

from __future__ import annotations

import asyncio
import logging
import os
import select
import sys
import threading
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

from app.agents.sampler import start_sampler
from app.cli.interactive_shell import alert_inbox as _alert_inbox
from app.cli.interactive_shell.alert_renderer import drain_and_render_incoming
from app.cli.interactive_shell.prompting import prompt_surface as _prompt_surface
from app.cli.interactive_shell.runtime import HotReloadCoordinator, ReplSession
from app.cli.interactive_shell.ui import ERROR, WARNING
from app.cli.support.exception_reporting import report_exception
from app.cli.support.prompt_support import repl_prompt_note_ctrl_c, repl_reset_ctrl_c_gate

from .dispatch import (
    DispatchCancelled,
    _build_cancel_key_bindings,
    _dispatch_needs_exclusive_stdin,
    _dispatch_one_turn,
    _dispatch_should_show_spinner,
    _install_session_key_bindings,
    _looks_like_cancel_request,
    _looks_like_confirmation_answer,
    _route_confirm_through_prompt,
)
from .state import _PROMPT_REFRESH_INTERVAL_S, ReplState, SpinnerState

log = logging.getLogger(__name__)


def _drain_stale_cpr_bytes() -> None:
    """Discard any CPR escape-sequence bytes left in stdin after a prompt_async teardown.

    When prompt_async returns (e.g. after the user types Y to confirm), the
    prompt_toolkit Application tears down its input-reader thread.  CPR responses
    (ESC[row;colR) that the bottom-toolbar refresh sent but that arrived just after
    the reader stopped are left sitting in the OS stdin buffer.  The *next*
    prompt_async call reads those bytes with a fresh vt100 parser, which has no
    open escape-sequence context; the bytes then appear as literal keystrokes in
    the input field.

    This function does a non-blocking drain of stdin between prompt_async calls —
    exactly when no Application is active and it is safe to read from stdin
    directly.  Only called on TTY stdin on POSIX; silently skipped otherwise.
    """
    if os.name == "nt" or not sys.stdin.isatty():
        return
    try:
        fd = sys.stdin.fileno()
        while select.select([fd], [], [], 0)[0]:
            chunk = os.read(fd, 256)
            if not chunk:
                break
    except OSError:
        pass


class StreamingConsole(Console):
    """Console adapter for streaming progress + cancellation checks."""

    def __init__(self, spinner: SpinnerState, cancel_event: threading.Event, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event

    def update_streaming_progress(self, bytes_received: int) -> None:
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()


async def run_interactive(
    session: ReplSession,
    hot_reloader: HotReloadCoordinator | None = None,
    pt_session: PromptSession[str] | None = None,
    inbox: _alert_inbox.AlertInbox | None = None,
) -> None:
    if pt_session is None:
        pt_session = _prompt_surface._build_prompt_session()
        session.prompt_history_backend = pt_session.history
    spinner = SpinnerState()
    state = ReplState()
    sampler_task = start_sampler()

    cancel_kb = _build_cancel_key_bindings(state)
    _install_session_key_bindings(pt_session, cancel_kb)

    pt_app = pt_session.app
    main_loop = asyncio.get_running_loop()
    state.bind_loop(main_loop)

    def _request_exit() -> None:
        state.request_exit()

        def _exit_prompt_app(attempts_left: int = 5) -> None:
            if pt_app.is_running:
                pt_app.exit()
                return
            if attempts_left > 0:
                main_loop.call_later(0.02, _exit_prompt_app, attempts_left - 1)

        main_loop.call_soon_threadsafe(_exit_prompt_app)

    async def _run_one_dispatch(text: str) -> None:
        dispatch_cancel = threading.Event()
        current_task = asyncio.current_task()
        if current_task is not None:
            state.start_dispatch(task=current_task, cancel_event=dispatch_cancel)
        else:
            state.current_cancel_event = dispatch_cancel
        console = StreamingConsole(
            spinner,
            dispatch_cancel,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        show_spinner = _dispatch_should_show_spinner(text, session)
        if show_spinner:
            spinner.start()
        try:
            await asyncio.to_thread(
                _dispatch_one_turn,
                text,
                session,
                console,
                on_exit=_request_exit,
                confirm_fn=lambda prompt: _route_confirm_through_prompt(state, prompt),
            )
        except asyncio.CancelledError:
            console.print(f"[{WARNING}]· interrupted[/]")
            raise
        except DispatchCancelled:
            console.print(f"[{WARNING}]· interrupted[/]")
        except Exception as exc:
            report_exception(exc, context="interactive_shell.dispatch_async")
            console.print(f"[{ERROR}]dispatch error:[/] {escape(str(exc))}")
        finally:
            if show_spinner:
                spinner.stop()
            state.finish_dispatch(dispatch_cancel)

    async def _alert_watcher() -> None:
        if inbox is None:
            return
        alert_console = Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        drain_and_render_incoming(session, alert_console, inbox)
        while not state.exit_requested:
            try:
                await asyncio.to_thread(inbox.pending_event.wait, timeout=1)
            except asyncio.CancelledError:
                return
            try:
                drain_and_render_incoming(session, alert_console, inbox)
            except Exception as exc:
                log.warning("Error draining incoming alerts: %s", exc)

    async def _processor() -> None:
        while not state.exit_requested:
            try:
                text = await state.queue.get()
            except asyncio.CancelledError:
                return
            if state.exit_requested:
                state.queue.task_done()
                return
            state.current_task = asyncio.create_task(_run_one_dispatch(text))
            try:
                await state.current_task
            except asyncio.CancelledError:
                # Expected when shutdown/cancel interrupts in-flight dispatch.
                pass
            except Exception as exc:
                log.debug("Processor task ended with dispatch exception: %s", exc)
            state.clear_current_task()
            state.queue.task_done()

    def _message_with_spinner() -> ANSI:
        base = _prompt_surface._prompt_message(session).value
        if state.is_awaiting_confirmation():
            confirm_text = state.confirm_prompt_text
            return ANSI(f"{confirm_text}\n{base}")
        return ANSI(f"{spinner.inline_spinner_ansi()}\n{base}")

    processor_task = asyncio.create_task(_processor())
    alert_watcher_task = asyncio.create_task(_alert_watcher())
    try:
        with patch_stdout(raw=True):
            echo_console = Console(highlight=False, force_terminal=True, color_system="truecolor")
            while True:
                if state.exit_requested:
                    return
                if inbox is not None:
                    try:
                        drain_and_render_incoming(session, echo_console, inbox)
                    except Exception as exc:
                        log.warning("Error draining alerts at turn start: %s", exc)

                if hot_reloader is not None and not state.is_dispatch_running():
                    hot_reloader.check_and_reload(echo_console)
                # Drain any CPR bytes (ESC[row;colR) left in stdin from the
                # previous prompt_async's bottom-toolbar refresh cycles.  Each
                # prompt_async call tears down its Application; responses that
                # arrive after the input-reader thread stops are left in the OS
                # buffer and would appear as literal keystrokes in the new
                # Application's fresh vt100 parser.  The brief sleep lets
                # in-transit terminal responses land before the non-blocking
                # drain runs; without it the terminal's write latency means
                # some bytes arrive after the drain and still corrupt input.
                await asyncio.sleep(0.05)
                _drain_stale_cpr_bytes()
                try:
                    text = await pt_session.prompt_async(
                        message=_message_with_spinner,
                        bottom_toolbar=spinner.toolbar_ansi,
                        refresh_interval=_PROMPT_REFRESH_INTERVAL_S,
                    )
                except EOFError:
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        continue
                    return
                except KeyboardInterrupt:
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        continue
                    if repl_prompt_note_ctrl_c(echo_console):
                        return
                    continue
                else:
                    repl_reset_ctrl_c_gate()

                if state.exit_requested:
                    return
                if state.is_dispatch_running() and _looks_like_cancel_request(text):
                    stripped = (text or "").strip()
                    _prompt_surface.render_submitted_prompt(echo_console, session, stripped)
                    state.cancel_current_dispatch()
                    continue

                if state.is_awaiting_confirmation():
                    if _looks_like_confirmation_answer(text):
                        state.deliver_confirmation(text or "")
                        continue
                    echo_console.print(
                        "[dim](type y/N to confirm the pending action; your input has been queued for after)[/]"
                    )
                    stripped = (text or "").strip()
                    if stripped:
                        _prompt_surface.render_submitted_prompt(echo_console, session, stripped)
                        await state.queue.put(stripped)
                    continue

                stripped = (text or "").strip()
                if not stripped:
                    continue
                _prompt_surface.render_submitted_prompt(echo_console, session, stripped)
                wait_for_dispatch = _dispatch_needs_exclusive_stdin(stripped, session)
                await state.queue.put(stripped)
                if wait_for_dispatch:
                    await state.queue.join()
    finally:
        state.request_exit()
        state.cancel_current_dispatch()
        sampler_task.cancel()
        try:  # noqa: SIM105
            await sampler_task
        except asyncio.CancelledError:
            # Expected during shutdown after explicit task cancellation.
            pass
        processor_task.cancel()
        alert_watcher_task.cancel()
        try:
            await processor_task
        except asyncio.CancelledError:
            # Expected during shutdown after explicit task cancellation.
            pass
        except Exception as exc:
            log.debug("Processor task shutdown raised exception: %s", exc)
        try:
            await alert_watcher_task
        except asyncio.CancelledError:
            # Expected during shutdown after explicit task cancellation.
            pass
        except Exception as exc:
            log.debug("Alert watcher shutdown raised exception: %s", exc)


_StreamingConsole = StreamingConsole
_run_interactive = run_interactive

__all__ = ["StreamingConsole", "run_interactive", "_StreamingConsole", "_run_interactive"]

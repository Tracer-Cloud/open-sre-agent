"""Prompt-toolkit runtime loop for interactive shell."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import select
import sys
import threading
from collections.abc import Callable
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
from app.cli.interactive_shell.runtime.dispatch import (
    DispatchCancelled,
    build_cancel_key_bindings,
    dispatch_needs_exclusive_stdin,
    dispatch_one_turn,
    dispatch_should_show_spinner,
    install_session_key_bindings,
    looks_like_cancel_request,
    looks_like_confirmation_answer,
    route_confirm_through_prompt,
)
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from app.cli.interactive_shell.ui import ERROR, WARNING
from app.cli.support.exception_reporting import report_exception
from app.cli.support.prompt_support import repl_prompt_note_ctrl_c, repl_reset_ctrl_c_gate
from app.cli.support.repl_progress import repl_safe_progress_scope

log = logging.getLogger(__name__)

_CPR_SEQUENCE_RE = re.compile(
    r"(?:\x1b\[|\x9b)\d{1,4};\d{1,4}R"  # ESC [ row ; col R
    r"|\[\d{1,4};\d{1,4}R"  # [row;colR without ESC (leaked into input)
    r"|\d{1,4};\d{1,4}R"  # row;colR without ESC or [
    r"|\d{1,4}R(?=[\[\d])"  # trailing rowR before another CPR fragment
)


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
        # Draining stdin is best-effort; ignore when the fd is not readable.
        pass


def _strip_cpr_sequences(text: str | None) -> str:
    """Remove terminal cursor-position replies that leaked into submitted text."""
    if not text:
        return ""
    return _CPR_SEQUENCE_RE.sub("", text)


def _contains_cpr_sequence(text: str | None) -> bool:
    return bool(text and _CPR_SEQUENCE_RE.search(text))


class StreamingConsole(Console):
    """Console adapter for streaming progress + cancellation checks."""

    def __init__(
        self,
        spinner: SpinnerState,
        cancel_event: threading.Event,
        *,
        prompt_invalidator: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event
        self._prompt_invalidator = prompt_invalidator

    def update_streaming_progress(self, bytes_received: int) -> None:
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def suppress_prompt_spinner(self) -> None:
        """Stop the REPL spinner before another live renderer owns the footer."""
        if not self._spinner.streaming:
            return
        self._spinner.stop()
        if self._prompt_invalidator is not None:
            self._prompt_invalidator()

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Reset the TTY column before each print when not streaming.

        Inline menus pad rows to the terminal width, leaving the cursor on a
        high column. Rich output that follows (tables, follow-up status lines,
        section rules) must start at column zero or lines appear broken.
        """
        if not self._spinner.streaming:
            from app.cli.interactive_shell.ui.choice_menu import (
                ensure_tty_column_zero,
                prepare_repl_output_line,
            )
            from app.cli.interactive_shell.ui.rendering import (
                _repl_output_already_prepared,
                _repl_table_width,
            )

            if not args and not kwargs:
                # ``console.print()`` is used for intentional blank spacer lines.
                # Only reset the column for those calls; do not prepend another
                # line break or they expand into double blank lines.
                ensure_tty_column_zero()
            elif not _repl_output_already_prepared():
                prepare_repl_output_line()
            if sys.stdout.isatty() and "width" not in kwargs:
                kwargs["width"] = _repl_table_width(self)
        super().print(*args, **kwargs)


async def run_interactive(
    session: ReplSession,
    pt_session: PromptSession[str] | None = None,
    inbox: _alert_inbox.AlertInbox | None = None,
) -> None:
    if pt_session is None:
        pt_session = _prompt_surface._build_prompt_session()
        session.prompt_history_backend = pt_session.history
    spinner = SpinnerState()
    state = ReplState()
    sampler_task = start_sampler()

    cancel_kb = build_cancel_key_bindings(state)
    install_session_key_bindings(pt_session, cancel_kb)

    pt_app = pt_session.app
    main_loop = asyncio.get_running_loop()
    state.bind_loop(main_loop)

    def _invalidate_prompt() -> None:
        main_loop.call_soon_threadsafe(pt_app.invalidate)

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
            prompt_invalidator=_invalidate_prompt,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        show_spinner = dispatch_should_show_spinner(text, session)
        if show_spinner:
            spinner.start()
        try:
            with repl_safe_progress_scope():
                await asyncio.to_thread(
                    dispatch_one_turn,
                    text,
                    session,
                    console,
                    on_exit=_request_exit,
                    confirm_fn=lambda prompt: route_confirm_through_prompt(state, prompt),
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
            # Investigation Rich Live + bottom-toolbar CPR can leave bytes in stdin;
            # drain before the next prompt_async so they are not typed into the field.
            await asyncio.sleep(0.05)
            _drain_stale_cpr_bytes()

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
        prefix = spinner.inline_spinner_ansi() or spinner.idle_hint_ansi()
        return ANSI(f"{prefix}\n{base}")

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

                # Drain any CPR bytes (ESC[row;colR) left in stdin from the
                # previous prompt_async's bottom-toolbar refresh cycles.
                # Each prompt_async tears down its Application; CPR responses
                # that arrive after the input-reader thread stops sit in the OS
                # stdin buffer and appear as literal keystrokes in the next
                # Application's fresh vt100 parser.
                # The brief sleep lets in-transit terminal responses land in the
                # buffer before the non-blocking select drain runs.
                await asyncio.sleep(0.05)
                _drain_stale_cpr_bytes()
                try:
                    text = await pt_session.prompt_async(
                        message=_message_with_spinner,
                        bottom_toolbar=spinner.toolbar_ansi,
                        refresh_interval=PROMPT_REFRESH_INTERVAL_S,
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
                    raw_text = text
                    text = _strip_cpr_sequences(text)
                    if not text.strip() and _contains_cpr_sequence(raw_text):
                        continue

                if state.exit_requested:
                    return
                if state.is_dispatch_running() and looks_like_cancel_request(text):
                    stripped = (text or "").strip()
                    _prompt_surface.render_submitted_prompt(echo_console, session, stripped)
                    state.cancel_current_dispatch()
                    continue

                if state.is_awaiting_confirmation():
                    if looks_like_confirmation_answer(text):
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
                wait_for_dispatch = dispatch_needs_exclusive_stdin(stripped, session)
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


__all__ = ["StreamingConsole", "run_interactive"]

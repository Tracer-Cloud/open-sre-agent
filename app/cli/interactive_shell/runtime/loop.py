"""Prompt-toolkit runtime loop for interactive shell."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell import alert_inbox as _alert_inbox
from app.cli.interactive_shell.alert_renderer import drain_and_render_incoming
from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.prompting import prompt_surface as _prompt_surface
from app.cli.interactive_shell.runtime.background_runner import drain_background_notices
from app.cli.interactive_shell.runtime.cpr_stdin import (
    contains_cpr_sequence,
    drain_stale_cpr_bytes,
    strip_cpr_sequences,
)
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
from app.cli.interactive_shell.runtime.repl_progress import repl_safe_progress_scope
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from app.cli.interactive_shell.runtime.streaming_console import StreamingConsole
from app.cli.interactive_shell.ui import ERROR, WARNING
from app.cli.interactive_shell.ui.prompt_support import (
    repl_prompt_note_ctrl_c,
    repl_reset_ctrl_c_gate,
)
from app.fleet_monitoring.sampler import start_sampler

log = logging.getLogger(__name__)


async def run_interactive(
    session: ReplSession,
    pt_session: PromptSession[str] | None = None,
    inbox: _alert_inbox.AlertInbox | None = None,
) -> None:
    session.schedule_warm_resolved_integrations()
    if pt_session is None:
        pt_session = _prompt_surface._build_prompt_session(session)
        session.prompt_history_backend = pt_session.history
    spinner = SpinnerState()
    state = ReplState()
    sampler_task = start_sampler()

    cancel_kb = build_cancel_key_bindings(state)
    install_session_key_bindings(pt_session, cancel_kb)

    pt_app = pt_session.app
    main_loop = asyncio.get_running_loop()
    state.bind_loop(main_loop)

    _invalidate_prompt = _prompt_surface.wire_prompt_refresh(session, pt_app, main_loop)

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
        from app.cli.interactive_shell.ui.output import set_prompt_suppress_fn

        show_spinner = dispatch_should_show_spinner(text, session)
        if show_spinner:
            spinner.start()
            set_prompt_suppress_fn(console.suppress_prompt_spinner)
        try:
            # Commands that take exclusive stdin ownership (e.g. bare
            # ``/investigate`` and other inline pickers) can safely use the
            # full Rich Live investigation stream because prompt_toolkit is not
            # actively reading input while we await ``state.queue.join()``.
            # Keep the REPL-safe append-only renderer for non-exclusive turns
            # to avoid Live redraw contention with the active prompt.
            progress_scope = (
                contextlib.nullcontext()
                if dispatch_needs_exclusive_stdin(text, session)
                else repl_safe_progress_scope()
            )
            with progress_scope:
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
            set_prompt_suppress_fn(None)
            if show_spinner:
                spinner.stop()
            state.finish_dispatch(dispatch_cancel)
            # Investigation Rich Live + bottom-toolbar CPR can leave bytes in stdin;
            # drain before the next prompt_async so they are not typed into the field.
            await asyncio.sleep(0.05)
            drain_stale_cpr_bytes()

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
        prefix = _prompt_surface.resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=spinner.idle_hint_ansi(),
        )
        return ANSI(f"{prefix}\n{base}")

    async def _spinner_ticker() -> None:
        # prompt_async's refresh_interval alone is not guaranteed to drive
        # visible prompt redraws while patch_stdout(raw=True) is active and
        # the LLM stream is writing rapidly.  This task explicitly invalidates
        # the prompt at 100 ms intervals so the braille glyph cycles smoothly.
        _TICK = 0.1
        while not state.exit_requested:
            try:
                await asyncio.sleep(_TICK)
            except asyncio.CancelledError:
                return
            if spinner.streaming:
                _invalidate_prompt()

    processor_task = asyncio.create_task(_processor())
    alert_watcher_task = asyncio.create_task(_alert_watcher())
    spinner_ticker_task = asyncio.create_task(_spinner_ticker())
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
                try:
                    drain_background_notices(session, echo_console)
                except Exception as exc:
                    log.warning("Error draining background notices at turn start: %s", exc)

                # Drain any CPR bytes (ESC[row;colR) left in stdin from the
                # previous prompt_async's bottom-toolbar refresh cycles.
                # Each prompt_async tears down its Application; CPR responses
                # that arrive after the input-reader thread stops sit in the OS
                # stdin buffer and appear as literal keystrokes in the next
                # Application's fresh vt100 parser.
                # The brief sleep lets in-transit terminal responses land in the
                # buffer before the non-blocking select drain runs.
                await asyncio.sleep(0.05)
                drain_stale_cpr_bytes()
                try:
                    prefilled = session.take_pending_prompt_default()
                    if prefilled and session.take_pending_autosubmit():
                        # An agent-queued command (e.g. /integrations setup) was
                        # set before this prompt opened; auto-submit it without
                        # waiting for input so it dispatches with exclusive stdin.
                        text = prefilled
                    else:
                        text = await pt_session.prompt_async(
                            message=_message_with_spinner,
                            bottom_toolbar=spinner.toolbar_ansi,
                            refresh_interval=PROMPT_REFRESH_INTERVAL_S,
                            placeholder=lambda: _prompt_surface.resolve_prompt_placeholder(session),
                            default=prefilled,
                        )
                except EOFError:
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        continue
                    if session.session_id:
                        echo_console.print()
                        echo_console.print("Resume this session with:")
                        echo_console.print(f"/resume {session.session_id}")
                        echo_console.print("Goodbye!")
                    return
                except KeyboardInterrupt:
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        continue
                    if repl_prompt_note_ctrl_c(echo_console, session.session_id):
                        return
                    continue
                else:
                    repl_reset_ctrl_c_gate()
                    raw_text = text
                    text = strip_cpr_sequences(text)
                    if not text.strip() and contains_cpr_sequence(raw_text):
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
        processor_task.cancel()
        alert_watcher_task.cancel()
        spinner_ticker_task.cancel()
        shutdown_labels = (
            "sampler",
            "processor",
            "alert watcher",
            "spinner ticker",
        )
        shutdown_results = await asyncio.gather(
            sampler_task,
            processor_task,
            alert_watcher_task,
            spinner_ticker_task,
            return_exceptions=True,
        )
        for label, result in zip(shutdown_labels, shutdown_results, strict=True):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.debug("%s task shutdown raised exception: %s", label, result)


__all__ = ["run_interactive"]

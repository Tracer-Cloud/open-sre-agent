"""Dispatch queue processing for the interactive REPL runtime."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable

from rich.markup import escape

from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.runtime.core.state import ReplState, SpinnerState
from interactive_shell.runtime.dispatch import (
    DispatchCancelled,
    dispatch_needs_exclusive_stdin,
    dispatch_one_turn,
    dispatch_should_show_spinner,
    route_confirm_through_prompt,
)
from interactive_shell.ui import ERROR, WARNING
from interactive_shell.ui.components.cpr_stdin import drain_stale_cpr_bytes
from interactive_shell.ui.output.repl_progress import repl_safe_progress_scope
from interactive_shell.ui.streaming.console import StreamingConsole
from interactive_shell.utils.error_handling.exception_reporting import report_exception

log = logging.getLogger(__name__)


class DispatchProcessor:
    """Own queued turn dispatch and per-turn cancellation lifecycle."""

    def __init__(
        self,
        session: ReplSession,
        state: ReplState,
        spinner: SpinnerState,
        *,
        prompt_invalidator: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        self.session = session
        self.state = state
        self.spinner = spinner
        self.prompt_invalidator = prompt_invalidator
        self.on_exit = on_exit

    async def run_queue(self) -> None:
        while not self.state.exit_requested:
            try:
                text = await self.state.queue.get()
            except asyncio.CancelledError:
                return
            if self.state.exit_requested:
                self.state.queue.task_done()
                return

            # Queued turns enter dispatch here; agent-routed turns continue
            # toward the LLM through dispatch/execution.
            dispatch_task = asyncio.create_task(self.run_one(text))
            self.state.current_task = dispatch_task
            try:
                await dispatch_task
            except asyncio.CancelledError:
                # Expected when shutdown/cancel interrupts in-flight dispatch.
                pass
            except Exception as exc:
                log.debug("Processor task ended with dispatch exception: %s", exc)
            self.state.clear_current_task()
            self.state.queue.task_done()

    async def run_one(self, text: str) -> None:
        dispatch_cancel = threading.Event()
        current_task = asyncio.current_task()
        if current_task is not None:
            self.state.start_dispatch(task=current_task, cancel_event=dispatch_cancel)
        else:
            self.state.current_cancel_event = dispatch_cancel

        console = StreamingConsole(
            self.spinner,
            dispatch_cancel,
            prompt_invalidator=self.prompt_invalidator,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        from interactive_shell.ui.output import set_prompt_suppress_fn

        show_spinner = dispatch_should_show_spinner(text, self.session)
        if show_spinner:
            self.spinner.start()
            set_prompt_suppress_fn(console.suppress_prompt_spinner)
        try:
            # Exclusive-stdin commands can safely use the full Rich Live stream
            # because prompt_toolkit is not concurrently reading the next input.
            progress_scope = (
                contextlib.nullcontext()
                if dispatch_needs_exclusive_stdin(text, self.session)
                else repl_safe_progress_scope()
            )
            with progress_scope:
                await asyncio.to_thread(
                    dispatch_one_turn,
                    text,
                    self.session,
                    console,
                    on_exit=self.on_exit,
                    confirm_fn=lambda prompt: route_confirm_through_prompt(self.state, prompt),
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
                self.spinner.stop()
            self.state.finish_dispatch(dispatch_cancel)
            await asyncio.sleep(0.05)
            drain_stale_cpr_bytes()

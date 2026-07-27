"""REPL subprocess presenter — Rich UI + session hooks for action tools."""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from platform.common.task_types import TaskKind
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING, print_command_output
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from surfaces.interactive_shell.utils.error_handling.exception_reporting import report_exception
from tools.interactive_shell.shared import ExecutionPolicyResult
from tools.interactive_shell.subprocess import SubprocessPresenter, subprocess_env_with_width

from .background_task_executor import (
    start_background_cli_task as _start_background_cli_task_default,
)
from .task_streaming import (
    _join_task_output_streams,
    _sr_resolve,
    _start_task_output_streams,
)

_MARKUP_STYLE_ALIASES: dict[str, str] = {
    "error": str(ERROR),
    "dim": str(DIM),
    "highlight": str(HIGHLIGHT),
    "warning": str(WARNING),
}

# Intentional Rich markup tags used by subprocess presenters and action tools.
_ALLOWED_MARKUP_TAG = re.compile(
    r"(\["
    r"(?:"
    r"#[0-9A-Fa-f]{6}|"
    r"bold|dim|highlight|warning|error|"
    r"/(?:bold|dim|highlight|warning|error)?"
    r")"
    r"\])"
)
_MARKUP_HINT = re.compile(r"\[(?:/?(?:error|dim|highlight|warning|bold)|/)\]")


def _expand_markup_aliases(message: str) -> str:
    for alias, token in _MARKUP_STYLE_ALIASES.items():
        message = message.replace(f"[{alias}]", f"[{token}]")
        message = message.replace(f"[/{alias}]", f"[/{token}]")
    return message


def _message_uses_intentional_markup(message: str) -> bool:
    if _MARKUP_HINT.search(message):
        return True
    return any(
        f"[{token}]" in message or f"[/{token}]" in message
        for token in _MARKUP_STYLE_ALIASES.values()
    )


def _escape_markup_message(message: str) -> str:
    """Escape plain-text segments while preserving intentional Rich markup tags."""
    expanded = _expand_markup_aliases(message)
    if not _message_uses_intentional_markup(expanded):
        return escape(expanded)
    parts = _ALLOWED_MARKUP_TAG.split(expanded)
    if len(parts) == 1:
        return escape(expanded)
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            rendered.append(part)
        elif part:
            rendered.append(escape(part))
    return "".join(rendered)


class ReplSubprocessPresenter:
    """Surface implementation of :class:`SubprocessPresenter` for the interactive REPL."""

    def __init__(
        self,
        session: Session,
        console: Console,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        action_already_listed: bool = False,
    ) -> None:
        self._session = session
        self._console = console
        self._confirm_fn = confirm_fn
        self._is_tty = is_tty
        self._action_already_listed = action_already_listed

    @property
    def session(self) -> Session:
        return self._session

    @property
    def console(self) -> Console:
        return self._console

    def execution_allowed(
        self,
        policy: ExecutionPolicyResult,
        *,
        action_summary: str,
    ) -> bool:
        return execution_allowed(
            policy,
            session=self._session,
            console=self._console,
            action_summary=action_summary,
            confirm_fn=self._confirm_fn,
            is_tty=self._is_tty,
            action_already_listed=self._action_already_listed,
        )

    def print(self, message: str = "") -> None:
        self._console.print(_escape_markup_message(message))

    def print_bold_command(self, display_command: str) -> None:
        self._console.print(f"[bold]$ {escape(display_command)}[/bold]")

    def print_command_output(self, text: str, *, style: str | None = None) -> None:
        resolved: str | None
        if style in _MARKUP_STYLE_ALIASES:
            resolved = _MARKUP_STYLE_ALIASES[style]
        elif style is None:
            resolved = None
        else:
            resolved = style
        print_command_output(self._console, text, style=resolved)

    def print_plain(self, text: str) -> None:
        self._console.print(Text(text))

    def report_exception(self, exc: BaseException, *, context: str) -> None:
        report_exception(exc, context=context)

    def subprocess_env(self) -> dict[str, str]:
        return subprocess_env_with_width(
            columns=self._console.size.width or 80,
            lines=self._console.size.height,
        )

    def start_task_output_streams(
        self,
        *,
        task: Any,
        proc: subprocess.Popen[Any],
        stdout_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
        stderr_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
    ) -> list[threading.Thread]:
        return _start_task_output_streams(
            task=task,
            proc=proc,
            console=self._console,
            stdout_capture=stdout_capture,
            stderr_capture=stderr_capture,
        )

    def join_task_output_streams(self, threads: list[threading.Thread]) -> None:
        _join_task_output_streams(threads)

    def start_background_cli_task(
        self,
        *,
        display_command: str,
        argv_list: list[str],
        timeout_seconds: int,
        kind: TaskKind = TaskKind.CLI_COMMAND,
        use_pty: bool = False,
    ) -> Any:
        starter = _sr_resolve("start_background_cli_task", _start_background_cli_task_default)
        return starter(
            display_command=display_command,
            argv_list=argv_list,
            session=self._session,
            console=self._console,
            timeout_seconds=timeout_seconds,
            kind=kind,
            use_pty=use_pty,
        )

    def print_error(self, message: str) -> None:
        self._console.print(f"[{ERROR}]{escape(message)}[/]")

    def print_highlight(self, message: str) -> None:
        self._console.print(f"[{HIGHLIGHT}]{escape(message)}[/]")


def make_repl_presenter(
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> SubprocessPresenter:
    """Construct a :class:`ReplSubprocessPresenter` for runners and tests."""
    return ReplSubprocessPresenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


__all__ = ["ReplSubprocessPresenter", "make_repl_presenter"]

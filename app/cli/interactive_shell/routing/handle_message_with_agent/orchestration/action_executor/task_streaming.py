"""Shared subprocess-streaming primitives, PTY helpers, and module-wide constants."""

from __future__ import annotations

import contextlib
import errno
import os
import re
import subprocess
import sys
import tempfile
import threading
from typing import IO, Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from app.cli.interactive_shell.runtime import TaskRecord
from app.cli.interactive_shell.ui import DIM, ERROR
from app.cli.support.exception_reporting import report_exception

# Full dotted name of the ``action_executor`` package. Submodules use this to
# look up patchable names from the parent namespace at call time so that tests
# using ``monkeypatch.setattr("…action_executor.X", fake)`` take effect even
# when the implementation lives in a submodule.
_ACTION_EXECUTOR_MODULE = (
    "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor"
)


def _ae_resolve(name: str, default: Any) -> Any:
    """Return ``action_executor.<name>`` if the package is loaded, else ``default``.

    Used by submodules to honour monkeypatches applied to the parent package
    namespace (e.g. ``monkeypatch.setattr("…action_executor.read_diag", …)``).
    """
    ae = sys.modules.get(_ACTION_EXECUTOR_MODULE)
    return getattr(ae, name, default) if ae is not None else default


SHELL_COMMAND_TIMEOUT_SECONDS = 120
SYNTHETIC_TEST_TIMEOUT_SECONDS = 1800
CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS = 1800
_SYNTHETIC_POLL_SECONDS = 0.25
_MAX_COMMAND_OUTPUT_CHARS = 24_000
_SYNTHETIC_DIAG_CHARS = 2_000  # max stderr bytes captured from a failing synthetic run
_SIGTERM_GRACE_SECONDS = 10  # wait for clean exit after SIGTERM before escalating to SIGKILL
_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS = 2
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mA-Za-z]")

# Width of the ``<task_id> <stream> │ `` prefix that ``_print_task_output_line``
# prepends to every relayed subprocess line. Used to align the subprocess's own
# Rich rendering width via ``COLUMNS`` so panels and tables don't wrap mid-row
# in the user's narrower terminal once the prefix has been added.
#   task_id (8 hex) + " " + stream ("stdout"/"stderr", 6) + " │ " (3) = 18
_TASK_OUTPUT_PREFIX_WIDTH = 18

# Below this many columns Rich panels and tables degrade past usefulness. We
# keep the subprocess at this minimum even if the user's terminal is tiny —
# wrapping the borders is no worse than crushing them.
_MIN_SUBPROCESS_TERMINAL_WIDTH = 60


def terminate_child_process(proc: subprocess.Popen[Any]) -> None:
    """Best-effort SIGTERM → wait → SIGKILL → wait without blocking forever."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError):
        proc.terminate()
    try:
        proc.wait(timeout=_SIGTERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def read_diag(buf: tempfile.SpooledTemporaryFile[bytes]) -> str:  # type: ignore[type-arg]
    """Read up to ``_SYNTHETIC_DIAG_CHARS`` bytes from a captured stderr buffer."""
    buf.seek(0)
    raw = buf.read(_SYNTHETIC_DIAG_CHARS).decode("utf-8", errors="replace").strip()
    return _ANSI_ESCAPE.sub("", raw)


def _print_task_output_line(
    console: Console,
    task: TaskRecord,
    stream_name: str,
    line: str,
    *,
    style: str | None = None,
) -> None:
    text = Text()
    text.append(f"{task.task_id} {stream_name} │ ", style=DIM)
    text.append(line.rstrip("\r\n"), style=style)
    console.print(text)


def _subprocess_env_with_aligned_width(console: Console) -> dict[str, str]:
    """Return ``os.environ`` patched so a piped Rich subprocess wraps to fit.

    Background: ``_print_task_output_line`` prepends ``<task_id> <stream> │ ``
    (a fixed 18-char prefix) to every relayed subprocess line before printing
    into the user's terminal. The subprocess itself sees a piped stdout, so
    Rich inside the subprocess falls back to a default 80-column rendering.
    The combined ``80 + 18 = 98`` chars then overflow narrower user terminals,
    breaking Rich's panel borders and table headers mid-row.

    We forward the user's actual terminal width minus the prefix overhead via
    the ``COLUMNS`` env var (and ``LINES`` for completeness). Rich and most
    POSIX tools honour ``COLUMNS`` via ``shutil.get_terminal_size``, so the
    subprocess renders narrow enough that the relayed line fits inside the
    user's terminal once the prefix is applied. A floor of 60 columns keeps
    rendering usable when the user's terminal is unusually narrow.
    """
    user_width = console.size.width or _MIN_SUBPROCESS_TERMINAL_WIDTH + _TASK_OUTPUT_PREFIX_WIDTH
    available = max(
        _MIN_SUBPROCESS_TERMINAL_WIDTH,
        user_width - _TASK_OUTPUT_PREFIX_WIDTH - 1,
    )
    env = dict(os.environ)
    env["COLUMNS"] = str(available)
    # LINES is less critical (Rich pagination is rare here) but we keep
    # the pair consistent so ``shutil.get_terminal_size`` agrees with itself.
    env.setdefault("LINES", str(max(20, console.size.height or 24)))
    return env


def _pump_task_stream(
    *,
    task: TaskRecord,
    stream_name: str,
    stream: IO[str],
    console: Console,
    style: str | None = None,
    capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
) -> None:
    try:
        for line in stream:
            if capture is not None:
                capture.write(line.encode("utf-8", errors="replace"))
            if line.strip():
                _print_task_output_line(console, task, stream_name, line, style=style)
                task.update_progress(line)
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context=f"interactive_shell.task_stream.{stream_name}")
        console.print(f"[{DIM}]task output stream ended unexpectedly:[/] {escape(str(exc))}")


def _start_task_output_streams(
    *,
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    console: Console,
    stdout_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
    stderr_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
) -> list[threading.Thread]:
    threads: list[threading.Thread] = []
    streams: tuple[tuple[str, IO[str] | None, str | None, Any], ...] = (
        ("stdout", proc.stdout, None, stdout_capture),
        ("stderr", proc.stderr, ERROR, stderr_capture),
    )
    for stream_name, stream, style, capture in streams:
        if stream is None:
            continue
        thread = threading.Thread(
            target=_pump_task_stream,
            kwargs={
                "task": task,
                "stream_name": stream_name,
                "stream": stream,
                "console": console,
                "style": style,
                "capture": capture,
            },
            daemon=True,
            name=f"task-output-{task.task_id}-{stream_name}",
        )
        thread.start()
        threads.append(thread)
    return threads


def _join_task_output_streams(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS)


def _console_file_is_tty(console: Console) -> bool:
    isatty = getattr(console.file, "isatty", None)
    return bool(isatty and isatty())


def _should_use_pty(console: Console, requested: bool) -> bool:
    return requested and hasattr(os, "openpty") and _console_file_is_tty(console)


def _pump_task_pty(
    *,
    master_fd: int,
    console: Console,
    capture: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
) -> None:
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError as exc:
                # BSD/macOS PTYs raise EIO at EOF; Linux commonly returns b"".
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            capture.write(chunk)
            console.file.write(chunk.decode("utf-8", errors="replace"))
            console.file.flush()
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context="interactive_shell.task_pty_stream")
        console.print(f"[{DIM}]task terminal stream ended unexpectedly:[/] {escape(str(exc))}")
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)


__all__ = [
    "SHELL_COMMAND_TIMEOUT_SECONDS",
    "SYNTHETIC_TEST_TIMEOUT_SECONDS",
    "CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS",
    "_SYNTHETIC_POLL_SECONDS",
    "_MAX_COMMAND_OUTPUT_CHARS",
    "_SYNTHETIC_DIAG_CHARS",
    "_TASK_OUTPUT_PREFIX_WIDTH",
    "_MIN_SUBPROCESS_TERMINAL_WIDTH",
    "_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS",
    "terminate_child_process",
    "read_diag",
    "_print_task_output_line",
    "_subprocess_env_with_aligned_width",
    "_pump_task_stream",
    "_start_task_output_streams",
    "_join_task_output_streams",
    "_console_file_is_tty",
    "_should_use_pty",
    "_pump_task_pty",
]

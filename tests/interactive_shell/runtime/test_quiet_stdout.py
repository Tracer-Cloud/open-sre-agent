"""Quiet shell stdout is buffered by the tool and painted by the REPL sink."""

from __future__ import annotations

from rich.console import Console

from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink
from tools.interactive_shell.quiet_stdout import (
    buffer_quiet_stdout,
    clear_quiet_stdout,
    take_quiet_stdout,
)


def setup_function() -> None:
    clear_quiet_stdout()


def test_paint_quiet_stdout_shows_buffered_text() -> None:
    # Arrange: a quiet command buffered stdout; the closer printed nothing.
    buffer_quiet_stdout("hi")
    lines: list[str] = []

    class _Console:
        def print(self, message: str = "", **_kwargs: object) -> None:
            lines.append(str(message))

    sink = ShellOutputSink(_Console())  # type: ignore[arg-type]

    # Act
    assert sink.paint_quiet_stdout() is True

    # Assert
    assert "hi" in "\n".join(lines)
    assert take_quiet_stdout() == ""


def test_paint_quiet_stdout_shows_outputless_success_marker() -> None:
    # Arrange: quiet ``touch`` buffered ✓ because there was no stdout to show live.
    buffer_quiet_stdout("✓")
    lines: list[str] = []

    class _Console:
        def print(self, message: str = "", **_kwargs: object) -> None:
            lines.append(str(message))

    sink = ShellOutputSink(_Console())  # type: ignore[arg-type]

    # Act / Assert
    assert sink.paint_quiet_stdout() is True
    assert "✓" in "\n".join(lines)
    assert take_quiet_stdout() == ""


def test_empty_print_clears_buffer_without_flashing_it() -> None:
    # Arrange: quiet probes ran, then an error path prints a blank spacer.
    buffer_quiet_stdout("Amsterdam: +18C")
    lines: list[str] = []

    class _Console:
        def print(self, message: str = "", **_kwargs: object) -> None:
            lines.append(str(message))

    sink = ShellOutputSink(_Console())  # type: ignore[arg-type]

    # Act
    sink.print()

    # Assert: spacer only — probes must not appear under the error.
    assert "\n".join(lines) == ""
    assert take_quiet_stdout() == ""
    assert "Amsterdam: +18C" not in "\n".join(lines)


def test_finalize_clears_leftover_quiet_stdout() -> None:
    buffer_quiet_stdout("leftover")
    sink = ShellOutputSink(Console(quiet=True))

    sink.finalize("ignored")

    assert take_quiet_stdout() == ""


def test_streamed_closing_clears_buffered_quiet_probes() -> None:
    # Arrange: quiet probes ran, then the closer streamed a composed answer.
    buffer_quiet_stdout("Amsterdam: +18C")
    buf_console = Console(record=True, quiet=True)
    sink = ShellOutputSink(buf_console)

    # Act
    sink.stream(label="assistant", chunks=["Amsterdam: sunny."])

    # Assert: the probes stay off the console; the buffer is cleared.
    assert take_quiet_stdout() == ""
    assert "Amsterdam: +18C" not in buf_console.export_text()


def test_response_header_clears_buffered_quiet_stdout() -> None:
    # Arrange: a generic tool answer is about to be painted.
    buffer_quiet_stdout("rate limit 4998")
    lines: list[str] = []

    class _Console:
        def print(self, message: str = "", **_kwargs: object) -> None:
            lines.append(str(message))

    sink = ShellOutputSink(_Console())  # type: ignore[arg-type]

    # Act
    sink.render_response_header("assistant")
    sink.print("3 failed, 59 succeeded")

    # Assert
    shown = "\n".join(lines)
    assert "3 failed, 59 succeeded" in shown
    assert "rate limit 4998" not in shown
    assert take_quiet_stdout() == ""

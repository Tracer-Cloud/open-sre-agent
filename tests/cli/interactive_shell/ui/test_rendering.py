"""Tests for Rich rendering helpers used by the interactive shell."""

from __future__ import annotations

import io
import re
import threading

import pytest
from rich.console import Console

from cli.interactive_shell.runtime.state import SpinnerState
from cli.interactive_shell.runtime.streaming_console import StreamingConsole
from cli.interactive_shell.ui.rendering import (
    _repl_write_buffer,
    print_repl_json,
    refresh_welcome_poster,
    repl_print,
    repl_render_launch_poster,
    repl_table,
)
from cli.interactive_shell.ui.tables import (
    print_planned_actions,
    render_integrations_table,
    render_mcp_table,
)


def test_repl_table_minimal_box() -> None:
    t = repl_table(title="T")
    assert t.title == "T"


def test_print_repl_json_tty_uses_single_buffered_write(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)

    console = Console(file=fake_stdout, force_terminal=True, width=80)
    print_repl_json(console, '{"ok": true}')

    assert len(fake_stdout.writes) == 1
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", fake_stdout.writes[0])
    assert rendered.startswith("\r\n")
    assert '"ok": true' in rendered


def test_render_integrations_table_empty_shows_hint() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    render_integrations_table(console, [])
    assert "opensre onboard" in buf.getvalue()


def test_repl_print_resets_before_each_line(monkeypatch) -> None:
    resets: list[bool] = []

    monkeypatch.setattr(
        "cli.interactive_shell.ui.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    repl_print(console, "line one")
    repl_print(console, "line two")

    assert len(resets) == 2


def test_repl_print_does_not_double_prepare_with_streaming_console(monkeypatch) -> None:
    resets: list[bool] = []

    monkeypatch.setattr(
        "cli.interactive_shell.ui.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    console = StreamingConsole(
        SpinnerState(),
        threading.Event(),
        file=io.StringIO(),
        force_terminal=False,
        width=80,
    )
    repl_print(console, "line")

    assert len(resets) == 1


def test_repl_print_streaming_console_prepares_tty_once_when_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr(
        "cli.interactive_shell.ui.choice_menu.repl_tty_interactive",
        lambda: True,
    )

    console = StreamingConsole(
        SpinnerState(),
        threading.Event(),
        file=io.StringIO(),
        force_terminal=False,
        width=80,
    )
    repl_print(console, "line")

    assert fake_stdout.writes == ["\r\n", "\r"]


def test_repl_render_launch_poster_uses_crlf_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)

    from cli.interactive_shell.ui.theme import set_active_theme

    set_active_theme("blue")
    console = Console(
        file=fake_stdout,
        force_terminal=True,
        highlight=False,
        color_system="truecolor",
        width=120,
    )
    repl_render_launch_poster(console, theme_notice="blue")

    written = "".join(fake_stdout.writes)
    assert "theme set:" in written
    assert "blue" in written
    assert "38;2;168;212;255" in written
    assert "185;237;175" not in written
    assert "opensre" in written
    assert "Welcome back" in written
    assert "\r\n" in written
    # REPL path must not emit bare \\n (causes double-spaced splash under patch_stdout).
    assert "\r" not in written.replace("\r\n", "")


def test_repl_write_buffer_strips_only_escaped_cpr_sequences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)

    _repl_write_buffer("\x1b[1;1Rtheme set: pink 12;5R\r\n")

    written = "".join(fake_stdout.writes)
    assert "theme set: pink" in written
    assert "12;5R" in written
    assert "\x1b[1;1R" not in written


def test_refresh_welcome_poster_drains_cpr_after_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    drains: list[str] = []

    monkeypatch.setattr(
        "cli.interactive_shell.ui.rendering.repl_clear_screen",
        lambda: drains.append("clear"),
    )
    monkeypatch.setattr(
        "cli.interactive_shell.runtime.cpr_stdin.drain_stale_cpr_bytes",
        lambda: drains.append("drain"),
    )
    monkeypatch.setattr(
        "cli.interactive_shell.ui.rendering.repl_render_launch_poster",
        lambda *_args, **_kwargs: drains.append("render"),
    )

    console = Console(file=io.StringIO(), force_terminal=False)
    refresh_welcome_poster(console)

    assert drains == ["clear", "drain", "render"]


def test_render_integrations_table_renders_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """print_repl_table on a non-TTY console writes via console.print to stdout.

    The cursor-reset (prepare_repl_output_line) is no longer called from the
    table rendering path; on a real TTY the blank line and \\r\\n normalisation
    are folded into a single sys.stdout.write call in print_repl_table.
    """
    console = Console(force_terminal=False, width=80)
    render_integrations_table(
        console,
        [
            {
                "service": "grafana",
                "source": "local store",
                "status": "passed",
                "detail": "Connected to https://example.grafana.net",
            }
        ],
    )

    assert "grafana" in capsys.readouterr().out


def test_render_integrations_table_sorts_services_and_includes_mcp(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console = Console(force_terminal=False, width=80)
    render_integrations_table(
        console,
        [
            {"service": "sentry", "source": "-", "status": "missing", "detail": "missing"},
            {"service": "github", "source": "-", "status": "missing", "detail": "missing"},
            {"service": "datadog", "source": "env", "status": "passed", "detail": "ok"},
        ],
    )

    output = capsys.readouterr().out
    assert output.index("datadog") < output.index("github") < output.index("sentry")
    assert "github" in output


def test_render_mcp_table_renders_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console = Console(force_terminal=False, width=80)
    render_mcp_table(
        console,
        [
            {
                "service": "github",
                "source": "local store",
                "status": "configured",
                "detail": "Connected",
            }
        ],
    )

    assert "github" in capsys.readouterr().out


def test_print_planned_actions_formats_kinds() -> None:
    from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
        PlannedAction,
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    print_planned_actions(
        console,
        [
            PlannedAction(kind="slash", content="/health", position=0),
            PlannedAction(kind="shell", content="pwd", position=10),
        ],
    )
    out = buf.getvalue()
    assert "/health" in out
    assert "pwd" in out

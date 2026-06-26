"""Slash commands for CLI parity, delegating to the Click CLI via subprocess."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from cli.interactive_shell.command_registry.suggestions import closest_choice
from cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    SYNTHETIC_TEST_TIMEOUT_SECONDS,
    start_background_cli_task,
)
from cli.interactive_shell.runtime import ReplSession, TaskKind
from cli.interactive_shell.ui import DIM, ERROR, print_command_output

_UPDATE_SUBPROCESS_TIMEOUT_SECONDS = 300
_BACKGROUND_TEST_SUBCOMMANDS = frozenset({"run", "synthetic", "cloudopsbench"})
_TEST_SUBCOMMANDS = ("list", "run", "synthetic", "cloudopsbench")
_TEST_PICKER_SELECTION_FILE_ENV = "OPENSRE_TEST_PICKER_SELECTION_FILE"


def _decode_subprocess_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_cli_command(
    console: Console,
    args: list[str],
    *,
    subprocess_timeout: float | None = None,
    capture_output: bool = False,
) -> bool:
    """Helper to delegate complex or interactive Click commands to a child process.

    ``subprocess_timeout`` caps how long ``subprocess.run`` waits before raising
    :class:`~subprocess.TimeoutExpired`. Interactive flows use ``None`` so the
    child can prompt as long as needed; callers that hit the network without a
    TTY (like ``opensre update``) pass a bounded timeout.

    ``capture_output`` (default ``False``) makes the helper capture stdout/stderr
    and replay them through ``console`` even without a timeout. Set this for
    non-interactive delegated commands (e.g. ``opensre tests list``) so their
    output appears inside the REPL buffer instead of bypassing ``console.print``
    via the child's inherited stdout FD. Interactive commands like ``onboard``
    must leave this ``False`` so the child's prompts stay attached to the real
    TTY. Capture is also enabled automatically whenever a timeout is set.

    Ctrl+C sends :exc:`KeyboardInterrupt`, which subclasses :exc:`BaseException`
    rather than :exc:`Exception`; it is handled here so the REPL survives and the
    child process exits on SIGINT alongside the interrupted ``run`` call.
    """
    console.print()
    cmd = [sys.executable, "-m", "cli", *args]
    should_capture = capture_output or subprocess_timeout is not None
    try:
        if should_capture:
            captured_result = subprocess.run(
                cmd,
                check=False,
                timeout=subprocess_timeout,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            print_command_output(console, captured_result.stdout or "")
            print_command_output(console, captured_result.stderr or "", style=ERROR)
            if captured_result.returncode != 0:
                console.print(
                    f"[{ERROR}]CLI command exited with non-zero code {captured_result.returncode}[/]"
                )
        else:
            interactive_result = subprocess.run(cmd, check=False)
            if interactive_result.returncode != 0:
                console.print(
                    f"[{ERROR}]CLI command exited with non-zero code {interactive_result.returncode}[/]"
                )
    except subprocess.TimeoutExpired as exc:
        print_command_output(console, _decode_subprocess_stream(exc.stdout))
        print_command_output(console, _decode_subprocess_stream(exc.stderr), style=ERROR)
        console.print(f"[{ERROR}]error:[/] CLI command timed out")
    except KeyboardInterrupt:
        console.print(f"[{DIM}]CLI command cancelled (Ctrl+C).[/]")
    except Exception as exc:
        console.print(f"[{ERROR}]error running CLI command:[/] {exc}")
    console.print()
    return True


def _cmd_onboard(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # The REPL loop adds ``/onboard`` to ``_WAIT_FOR_COMPLETION_COMMANDS``
    # (dispatch.py) so the prompt_toolkit Application is torn down before
    # this handler runs — the wizard subprocess therefore gets exclusive
    # stdin and can drive its own interactive prompts without conflicting
    # with the shell's UI.
    return run_cli_command(console, ["onboard", *args])


def _cmd_remote(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["remote", *args])


def _catalog_task_kind(command: list[str]) -> TaskKind:
    return TaskKind.SYNTHETIC_TEST if "synthetic" in command else TaskKind.CLI_COMMAND


def _argv_for_catalog_command(command: list[str]) -> list[str]:
    if command[:1] == ["opensre"]:
        return [sys.executable, "-m", "cli", *command[1:]]
    return command


def _start_test_command(
    *,
    session: ReplSession,
    console: Console,
    command: list[str],
    display_command: str | None = None,
) -> None:
    shown = display_command or shlex.join(command)
    session.record("cli_command", shown)
    start_background_cli_task(
        display_command=shown,
        argv_list=_argv_for_catalog_command(command),
        session=session,
        console=console,
        timeout_seconds=SYNTHETIC_TEST_TIMEOUT_SECONDS,
        kind=_catalog_task_kind(command),
        use_pty=True,
    )


def _run_test_picker_for_background(session: ReplSession, console: Console) -> bool:
    console.print()
    with contextlib.closing(
        tempfile.NamedTemporaryFile(
            prefix="opensre-test-selection-",
            suffix=".json",
            delete=False,
        )
    ) as handle:
        selection_path = Path(handle.name)
    try:
        env = dict(os.environ)
        env[_TEST_PICKER_SELECTION_FILE_ENV] = str(selection_path)
        result = subprocess.run(
            [sys.executable, "-m", "cli", "tests"],
            check=False,
            env=env,
        )
        if result.returncode != 0:
            console.print(f"[{ERROR}]CLI command exited with non-zero code {result.returncode}[/]")
            console.print()
            return True
        if not selection_path.stat().st_size:
            console.print()
            return True
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
    finally:
        with contextlib.suppress(OSError):
            selection_path.unlink()

    if not isinstance(payload, list):
        console.print(f"[{ERROR}]test picker returned an invalid selection[/]")
        console.print()
        return True

    for item in payload:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            continue
        display = item.get("command_display")
        _start_test_command(
            session=session,
            console=console,
            command=command,
            display_command=display if isinstance(display, str) else None,
        )
    console.print()
    return True


def _cmd_tests(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        return _run_test_picker_for_background(session, console)

    subcommand = args[0].lower()
    if subcommand in _BACKGROUND_TEST_SUBCOMMANDS:
        _start_test_command(
            session=session,
            console=console,
            command=["opensre", "tests", *args],
        )
        return True

    if subcommand.startswith("-"):
        return run_cli_command(console, ["tests", *args], capture_output=True)

    if subcommand not in _TEST_SUBCOMMANDS:
        suggestion = closest_choice(subcommand, _TEST_SUBCOMMANDS)
        if suggestion is None:
            console.print(
                f"[{ERROR}]unknown tests subcommand:[/] {escape(args[0])}  "
                "(try [bold]/tests list[/bold], [bold]/tests run <test_id>[/bold], "
                "[bold]/tests synthetic[/bold], or [bold]/tests cloudopsbench[/bold])"
            )
        else:
            console.print(
                f"[{ERROR}]unknown tests subcommand:[/] {escape(args[0])}  "
                f"Did you mean [bold]/tests {suggestion}[/bold]?"
            )
        session.mark_latest(ok=False, kind="slash")
        return True

    return run_cli_command(console, ["tests", *args], capture_output=True)


def _cmd_guardrails(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # ``opensre guardrails`` and its subcommands are all non-interactive printers
    # (init/test/audit/rules just ``click.echo``). Capture so the output — and
    # Click's usage block when no subcommand is given — reaches the REPL buffer
    # instead of bypassing ``console.print`` via the child's inherited stdout FD.
    return run_cli_command(console, ["guardrails", *args], capture_output=True)


def _cmd_update(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(
        console,
        ["update", *args],
        subprocess_timeout=_UPDATE_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _cmd_uninstall(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["uninstall", *args])


def _cmd_config(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # Non-interactive click.echo only; capture so output reaches the REPL buffer
    # instead of the child's inherited stdout while prompt_toolkit redraws.
    return run_cli_command(console, ["config", *args], capture_output=True)


def _cmd_messaging(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["messaging", *args])


def _cmd_hermes(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["hermes", *args])


def _cmd_cron(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["cron", *args])


def _cmd_watchdog(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["watchdog", *args])


def _cmd_debug(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["debug", *args])


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/onboard",
        "Run the interactive onboarding wizard.",
        _cmd_onboard,
        usage=("/onboard", "/onboard local_llm"),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/remote",
        "Connect to and trigger a remote deployed agent.",
        _cmd_remote,
        usage=(
            "/remote health",
            "/remote investigate",
            "/remote ops",
            "/remote pull",
            "/remote trigger",
        ),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/tests",
        "Browse and run inventoried tests.",
        _cmd_tests,
        usage=("/tests", "/tests list", "/tests run", "/tests synthetic"),
        first_arg_completions=tuple((name, f"/tests {name}") for name in _TEST_SUBCOMMANDS),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/guardrails",
        "Manage sensitive information guardrail rules.",
        _cmd_guardrails,
        usage=(
            "/guardrails audit",
            "/guardrails init",
            "/guardrails rules",
            "/guardrails test",
        ),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/update",
        "Check for a newer version and update if available.",
        _cmd_update,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/uninstall",
        "Remove OpenSRE and all local data from this machine.",
        _cmd_uninstall,
        execution_tier=ExecutionTier.ELEVATED,
    ),
    SlashCommand(
        "/config",
        "Show or edit local OpenSRE config.",
        _cmd_config,
        usage=("/config show", "/config set <key> <value>"),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/messaging",
        "Manage messaging security and identities.",
        _cmd_messaging,
        usage=(
            "/messaging pair",
            "/messaging allow",
            "/messaging revoke",
            "/messaging status",
        ),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/hermes",
        "Live-tail Hermes logs and route incidents to Telegram.",
        _cmd_hermes,
        usage=("/hermes watch",),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/cron",
        "Manage cron-driven scheduled deliveries.",
        _cmd_cron,
        usage=("/cron list", "/cron add", "/cron remove <id>", "/cron run <id>", "/cron logs <id>"),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/watchdog",
        "Monitor one process and send threshold alarms.",
        _cmd_watchdog,
        usage=("/watchdog --pid <pid> [--max-rss <size>] [--max-cpu <percent>]",),
        examples=("/watchdog --pid 123 --max-rss 1G",),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/debug",
        "run targeted runtime diagnostics",
        _cmd_debug,
        execution_tier=ExecutionTier.SAFE,
    ),
]

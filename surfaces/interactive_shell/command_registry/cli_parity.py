"""Slash commands for CLI parity, delegating to the Click CLI via subprocess."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from core.agent_harness.session.terminal_access import (
    session_terminal,
    set_turn_outcome_hint,
)
from surfaces.interactive_shell.command_registry.suggestions import closest_choice
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session, TaskKind
from surfaces.interactive_shell.runtime.subprocess_runner import (
    SYNTHETIC_TEST_TIMEOUT_SECONDS,
    build_opensre_cli_argv,
    start_background_cli_task,
)
from surfaces.interactive_shell.ui import DIM, ERROR, print_command_output
from surfaces.interactive_shell.utils.telemetry.turn_outcome import format_wizard_cli_outcome

_UPDATE_SUBPROCESS_TIMEOUT_SECONDS = 300
_BACKGROUND_TEST_SUBCOMMANDS = frozenset({"run", "synthetic", "cloudopsbench"})
_TEST_SUBCOMMANDS = ("list", "run", "synthetic", "cloudopsbench")
_TEST_PICKER_SELECTION_FILE_ENV = "OPENSRE_TEST_PICKER_SELECTION_FILE"
_PARENT_INTERACTIVE_SHELL_ENV = "OPENSRE_PARENT_INTERACTIVE_SHELL"
_HEADLESS_CLI_SUBPROCESS_TIMEOUT_SECONDS = 90.0


def publish_headless_slash_response(
    session: Session,
    *,
    message: str,
    ok: bool = True,
) -> None:
    """Pin an explicit slash reply for gateway/headless surfaces (wizards, setup)."""
    session.complete_latest_record(
        "slash",
        response_text=message.strip(),
        ok=ok,
        slash_outcome="headless_guidance",
    )


def _decode_subprocess_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _cli_command_succeeded(exit_code: int | None) -> bool:
    return exit_code == 0


def run_cli_command(
    console: Console,
    args: list[str],
    *,
    session: Session | None = None,
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

    **Return value:** Reports subprocess success for headless/gateway sessions
    (``session`` with no terminal facet) so slash analytics can show failure.
    On the interactive REPL, always returns ``True`` so delegated CLI failures
    do not propagate to :func:`dispatch_slash` and exit the shell — failures
    are still recorded via :meth:`Session.mark_latest`.

    Ctrl+C sends :exc:`KeyboardInterrupt`, which subclasses :exc:`BaseException`
    rather than :exc:`Exception`; it is handled here so the REPL survives and the
    child process exits on SIGINT alongside the interrupted ``run`` call.
    """
    console.print()
    cmd = build_opensre_cli_argv(args)
    headless = session is not None and session_terminal(session) is None
    should_capture = capture_output or subprocess_timeout is not None or headless
    if headless and subprocess_timeout is None:
        subprocess_timeout = _HEADLESS_CLI_SUBPROCESS_TIMEOUT_SECONDS
    child_env = os.environ.copy()
    child_env[_PARENT_INTERACTIVE_SHELL_ENV] = "1"
    if should_capture:
        # Captured child stdout isn't a TTY, so force Rich colour there and parse
        # it back in print_command_output — otherwise its styling would be lost.
        child_env["FORCE_COLOR"] = "1"
    exit_code: int | None = 0
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
                env=child_env,
            )
            exit_code = captured_result.returncode
            print_command_output(console, captured_result.stdout or "")
            print_command_output(console, captured_result.stderr or "", style=ERROR)
            if captured_result.returncode != 0:
                console.print(
                    f"[{ERROR}]CLI command exited with non-zero code {captured_result.returncode}[/]"
                )
        else:
            interactive_result = subprocess.run(cmd, check=False, env=child_env)
            exit_code = interactive_result.returncode
            if interactive_result.returncode != 0:
                console.print(
                    f"[{ERROR}]CLI command exited with non-zero code {interactive_result.returncode}[/]"
                )
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        print_command_output(console, _decode_subprocess_stream(exc.stdout))
        print_command_output(console, _decode_subprocess_stream(exc.stderr), style=ERROR)
        console.print(f"[{ERROR}]error:[/] CLI command timed out")
    except KeyboardInterrupt:
        exit_code = None
        console.print(f"[{DIM}]CLI command cancelled (Ctrl+C).[/]")
    except Exception as exc:
        exit_code = None
        console.print(f"[{ERROR}]error running CLI command:[/] {exc}")
    console.print()
    if session is not None and not should_capture:
        set_turn_outcome_hint(session, format_wizard_cli_outcome(args, exit_code=exit_code))
    ok = _cli_command_succeeded(exit_code)
    if session is not None and not ok:
        session.mark_latest(ok=False, kind="slash")
    # Headless/gateway surfaces need the real exit status for slash analytics.
    # Interactive REPL handlers must not return False to dispatch_slash on CLI
    # failure — that would exit the shell (/exit is the only intentional False).
    return ok if headless else True


def _cmd_onboard(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if session_terminal(session) is None:
        cli_cmd = " ".join(["uv run opensre onboard", *args]).strip()
        message = (
            "Onboarding is an interactive wizard (LLM provider, integrations, messaging). "
            "It cannot run inside a Telegram chat.\n\n"
            f"Run on the server:\n  {cli_cmd}\n\n"
            "Or configure individual services with "
            "`/integrations setup <service>`."
        )
        console.print()
        console.print(message)
        publish_headless_slash_response(session, message=message)
        return True
    # The REPL loop treats ``/onboard`` as exclusive-stdin in
    # ``runtime.utils.input_policy`` so the prompt_toolkit Application is torn down before
    # this handler runs — the wizard subprocess therefore gets exclusive
    # stdin and can drive its own interactive prompts without conflicting
    # with the shell's UI.
    return run_cli_command(console, ["onboard", *args], session=session)


def _cmd_auth(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    capture_output = not args or args[0].lower() in {"status", "logout"}
    return run_cli_command(console, ["auth", *args], capture_output=capture_output, session=session)


def _cmd_login(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["auth", "login", *args], session=session)


def _cmd_remote(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["remote", *args], session=session)


def _catalog_task_kind(command: list[str]) -> TaskKind:
    return TaskKind.SYNTHETIC_TEST if "synthetic" in command else TaskKind.CLI_COMMAND


def _argv_for_catalog_command(command: list[str]) -> list[str]:
    if command[:1] == ["opensre"]:
        return build_opensre_cli_argv(command[1:])
    return command


def _start_test_command(
    *,
    session: Session,
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


def _run_test_picker_for_background(session: Session, console: Console) -> bool:
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
            build_opensre_cli_argv(["tests"]),
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


def _cmd_tests(session: Session, console: Console, args: list[str]) -> bool:
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


def _cmd_guardrails(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # ``opensre guardrails`` and its subcommands are all non-interactive printers
    # (init/test/audit/rules just ``click.echo``). Capture so the output — and
    # Click's usage block when no subcommand is given — reaches the REPL buffer
    # instead of bypassing ``console.print`` via the child's inherited stdout FD.
    return run_cli_command(console, ["guardrails", *args], capture_output=True)


def _cmd_update(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(
        console,
        ["update", *args],
        subprocess_timeout=_UPDATE_SUBPROCESS_TIMEOUT_SECONDS,
        session=session,
    )


def _cmd_uninstall(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["uninstall", *args])


def _cmd_config(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # Non-interactive click.echo only; capture so output reaches the REPL buffer
    # instead of the child's inherited stdout while prompt_toolkit redraws.
    return run_cli_command(console, ["config", *args], capture_output=True)


def _cmd_messaging(session: Session, console: Console, args: list[str]) -> bool:
    # Non-interactive subcommands: capture so output renders through the REPL
    # (inherited stdout gets clipped by prompt_toolkit's screen management).
    return run_cli_command(console, ["messaging", *args], capture_output=True, session=session)


def _cmd_hermes(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["hermes", *args])


def _cmd_cron(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["cron", *args])


def _cmd_watchdog(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["watchdog", *args])


def _cmd_debug(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["debug", *args])


def _cmd_misses(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # Non-interactive printers only (list/stats/export/convert) — capture so the
    # output reaches the REPL buffer instead of the child's inherited stdout.
    return run_cli_command(console, ["misses", *args], capture_output=True)


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/auth",
        "Log in to LLM providers and inspect local auth state.",
        _cmd_auth,
        usage=("/auth", "/auth status", "/auth login deepseek", "/auth logout deepseek"),
    ),
    SlashCommand(
        "/login",
        "Shortcut for LLM provider login.",
        _cmd_login,
        usage=("/login", "/login chatgpt", "/login claude", "/login deepseek"),
    ),
    SlashCommand(
        "/onboard",
        "Run the interactive onboarding wizard.",
        _cmd_onboard,
        usage=("/onboard", "/onboard local_llm"),
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
    ),
    SlashCommand(
        "/tests",
        "Browse and run inventoried tests.",
        _cmd_tests,
        usage=("/tests", "/tests list", "/tests run", "/tests synthetic"),
        first_arg_completions=tuple((name, f"/tests {name}") for name in _TEST_SUBCOMMANDS),
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
    ),
    SlashCommand(
        "/update",
        "Check for a newer version and update if available.",
        _cmd_update,
    ),
    SlashCommand(
        "/uninstall",
        "Remove OpenSRE and all local data from this machine.",
        _cmd_uninstall,
    ),
    SlashCommand(
        "/config",
        "Show or edit local OpenSRE config.",
        _cmd_config,
        usage=("/config show", "/config set <key> <value>"),
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
    ),
    SlashCommand(
        "/hermes",
        "Live-tail Hermes logs and send incidents to Telegram.",
        _cmd_hermes,
        usage=("/hermes watch",),
    ),
    SlashCommand(
        "/cron",
        "Manage cron-driven scheduled deliveries.",
        _cmd_cron,
        usage=("/cron list", "/cron add", "/cron remove <id>", "/cron run <id>", "/cron logs <id>"),
    ),
    SlashCommand(
        "/watchdog",
        "Monitor one process and send threshold alarms.",
        _cmd_watchdog,
        usage=("/watchdog --pid <pid> [--max-rss <size>] [--max-cpu <percent>]",),
        examples=("/watchdog --pid 123 --max-rss 1G",),
    ),
    SlashCommand(
        "/debug",
        "run targeted runtime diagnostics",
        _cmd_debug,
    ),
    SlashCommand(
        "/misses",
        "Triage investigation misses and export them as benchmark scenarios.",
        _cmd_misses,
        usage=(
            "/misses list",
            "/misses stats",
            "/misses export --out <dir>",
            "/misses convert <miss_id>",
        ),
    ),
]

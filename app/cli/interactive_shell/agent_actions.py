"""Deterministic actions for the interactive terminal assistant."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from app.cli.interactive_shell.commands import dispatch_slash, switch_llm_provider
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.shell_execution import execute_shell_command
from app.cli.interactive_shell.shell_policy import evaluate_policy, parse_shell_command
from app.cli.interactive_shell.tasks import TaskKind, TaskRecord
from app.cli.interactive_shell.terminal_intent import mentioned_integration_services
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD
from app.cli.support.errors import OpenSREError


@dataclass(frozen=True)
class PlannedAction:
    """A deterministic action inferred from a natural-language terminal request."""

    kind: Literal["llm_provider", "slash", "shell", "sample_alert", "synthetic_test"]
    content: str
    position: int


@dataclass(frozen=True)
class PromptClause:
    """A single clause from a compound natural-language prompt."""

    text: str
    position: int


_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:check|verify|show|get|run)\b.{0,80}?\b(?:health|status)\b"
            r"|"
            r"\bopensre\s+health\b",
            re.IGNORECASE,
        ),
        "/health",
    ),
    (
        re.compile(
            r"\b(?:show|list|get|which|what)\b.{0,80}?"
            r"\b(?:connected\s+)?(?:services|integrations)\b",
            re.IGNORECASE,
        ),
        "/list integrations",
    ),
    (
        re.compile(
            r"\b(?:show|tell\s+me|get|what(?:'s|\s+is)?|current)\b.{0,80}?"
            r"\b(?:cli\s+)?version\b"
            r"|"
            r"\bopensre\s+version\b",
            re.IGNORECASE,
        ),
        "/version",
    ),
)

_SAMPLE_ALERT_RE = re.compile(
    r"\b(?:try|run|start|launch|fire|send|trigger)\b.{0,60}?"
    r"\b(?:sample|simple|test|demo)\s+(?:alert|event)\b",
    re.IGNORECASE,
)
_SYNTHETIC_RDS_TEST_RE = re.compile(
    r"\b(?:run|start|launch|execute)\b.{0,80}?"
    r"\b(?:synthetic|benchmark|test)\b.{0,80}?"
    r"\b(?:r\s*d\s*s|postgres(?:ql)?|database|db)\b",
    re.IGNORECASE | re.DOTALL,
)
_LLM_PROVIDER_NAMES = frozenset(
    {
        "anthropic",
        "openai",
        "openrouter",
        "requesty",
        "gemini",
        "nvidia",
        "ollama",
        "codex",
        "claude-code",
        "gemini-cli",
    }
)
_LLM_PROVIDER_RE = re.compile(
    rf"\b(?P<provider>{'|'.join(sorted(_LLM_PROVIDER_NAMES, key=len, reverse=True))})\b",
    re.IGNORECASE,
)
_LLM_PROVIDER_SWITCH_RE = re.compile(
    r"\b(?:switch|change|set|use|select)\b.{0,120}?\b(?:llm|model|provider)\b"
    r"|"
    r"\b(?:switch|change|use|select)\s+(?:to|over\s+to)\b",
    re.IGNORECASE | re.DOTALL,
)

_INTEGRATION_DETAIL_RE = re.compile(
    r"\b(tell\s+me|show|list|get|what)\b.{0,120}?"
    r"\b(integrations?|services?|connections?|connected|configured|credentials?)\b",
    re.IGNORECASE,
)

_INTEGRATION_CAPABILITY_RE = re.compile(
    r"\b(what\b.{0,60}\bcan\s+do|can\s+do|does|about)\b",
    re.IGNORECASE,
)

_INTEGRATION_CONFIG_DETAIL_RE = re.compile(
    r"\b(show|list|get|connections?|connected|configured|credentials?)\b",
    re.IGNORECASE,
)

_CLAUSE_SPLIT_RE = re.compile(r"\s+\b(?:and(?:\s+then)?|then)\b\s+", re.IGNORECASE)
_EXPLICIT_SHELL_RE = re.compile(
    r"^\s*(?:please\s+)?(?:run|execute|exec)\s+"
    r"(?:this\s+)?(?:the\s+)?(?:shell\s+)?(?:command\s+)?(?::\s*)?(?P<command>.+?)\s*$",
    re.IGNORECASE,
)
_SHELL_PROMPT_RE = re.compile(r"^\s*\$\s+(?P<command>.+?)\s*$")
_NON_COMMAND_STARTS = frozenset(
    {
        "can",
        "could",
        "explain",
        "hello",
        "hey",
        "hi",
        "how",
        "please",
        "show",
        "tell",
        "thanks",
        "thank",
        "what",
        "when",
        "where",
        "which",
        "why",
    }
)
# Shell builtins that may not be discoverable via `shutil.which()` on all platforms.
# Keep this list intentionally small and add tests when extending it.
_SHELL_BUILTINS = frozenset({"cd", "pwd"})
_SHELL_COMMAND_TIMEOUT_SECONDS = 120
_SYNTHETIC_TEST_TIMEOUT_SECONDS = 1800
_SYNTHETIC_POLL_SECONDS = 0.25
_MAX_COMMAND_OUTPUT_CHARS = 24_000
_SYNTHETIC_DIAG_CHARS = 2_000  # max stderr bytes captured from a failing synthetic run
_SIGTERM_GRACE_SECONDS = 10  # wait for clean exit after SIGTERM before escalating to SIGKILL
_IS_WINDOWS = os.name == "nt"


def _slash_action(command: str, position: int) -> PlannedAction:
    return PlannedAction(kind="slash", content=command, position=position)


def _shell_action(command: str, position: int) -> PlannedAction:
    return PlannedAction(kind="shell", content=command, position=position)


def _sample_alert_action(template_name: str, position: int) -> PlannedAction:
    return PlannedAction(kind="sample_alert", content=template_name, position=position)


def _synthetic_test_action(suite_name: str, position: int) -> PlannedAction:
    return PlannedAction(kind="synthetic_test", content=suite_name, position=position)


def _llm_provider_action(provider: str, position: int) -> PlannedAction:
    return PlannedAction(kind="llm_provider", content=provider, position=position)


def _strip_wrapping_quotes(command: str) -> str:
    stripped = command.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"`", "'", '"'}:
        return stripped[1:-1].strip()
    return stripped


def _normalize_shell_command(command: str) -> str | None:
    normalized = _strip_wrapping_quotes(command)
    if not normalized or "\n" in normalized or "\r" in normalized:
        return None
    lower = normalized.lower()
    if lower.startswith(("a ", "an ")) or "investigation" in lower:
        return None
    return normalized


def _first_command_token(command: str) -> str | None:
    try:
        tokens = shlex.split(command, posix=not _IS_WINDOWS)
    except ValueError:
        # `shlex` in POSIX mode treats `\` as an escape character, which breaks
        # common Windows paths such as `cd C:\` (trailing backslash).
        try:
            tokens = shlex.split(command, posix=False)
        except ValueError:
            return None
    if not tokens:
        return None
    return tokens[0]


def _looks_like_direct_shell_command(text: str) -> bool:
    first = _first_command_token(text)
    if first is None:
        return False
    if first.lower() in _NON_COMMAND_STARTS:
        return False
    if first.lower() in _SHELL_BUILTINS:
        return True
    if first.startswith(("./", "../", "/")):
        return Path(first).exists()
    return shutil.which(first) is not None


def _extract_shell_command(clause: PromptClause) -> PlannedAction | None:
    prompt_match = _SHELL_PROMPT_RE.match(clause.text)
    if prompt_match is not None:
        command = _normalize_shell_command(prompt_match.group("command"))
        return (
            _shell_action(command, clause.position + prompt_match.start("command"))
            if command
            else None
        )

    explicit_match = _EXPLICIT_SHELL_RE.match(clause.text)
    if explicit_match is not None:
        command = _normalize_shell_command(explicit_match.group("command"))
        if command is None:
            return None
        return _shell_action(command, clause.position + explicit_match.start("command"))

    command = _normalize_shell_command(clause.text)
    if command is not None and command.startswith("!") and len(command) > 1:
        return _shell_action(command, clause.position)
    if command is not None and _looks_like_direct_shell_command(command):
        return _shell_action(command, clause.position)
    return None


def _split_prompt_clauses(message: str) -> list[PromptClause]:
    """Split compound prompts while preserving each clause's source position."""
    clauses: list[PromptClause] = []
    start = 0
    for match in _CLAUSE_SPLIT_RE.finditer(message):
        raw = message[start : match.start()]
        stripped = raw.strip()
        if stripped:
            clauses.append(PromptClause(text=stripped, position=start + raw.index(stripped)))
        start = match.end()

    raw = message[start:]
    stripped = raw.strip()
    if stripped:
        clauses.append(PromptClause(text=stripped, position=start + raw.index(stripped)))

    return clauses or [PromptClause(text=message.strip(), position=0)]


def _plan_clause_actions(
    clause: PromptClause,
    *,
    seen_slash: set[str],
) -> list[PlannedAction]:
    planned: list[PlannedAction] = []
    mentioned_services = mentioned_integration_services(clause.text)

    for pattern, command in _ACTION_PATTERNS:
        match = pattern.search(clause.text)
        if match is None or command in seen_slash:
            continue
        if command == "/list integrations" and mentioned_services:
            continue
        planned.append(_slash_action(command, clause.position + match.start()))
        seen_slash.add(command)

    lower = clause.text.lower()
    for service in mentioned_services:
        match = re.search(rf"\b{re.escape(service.replace('_', ' '))}\b", lower)
        position = clause.position + (match.start() if match else 0)

        # Capability questions should get an answer, not only configured-status output.
        relative_position = position - clause.position
        window_start = max(0, relative_position - 80)
        window_end = min(len(clause.text), relative_position + 120)
        window = clause.text[window_start:window_end]
        detail_window = clause.text[
            max(0, relative_position - 30) : min(len(clause.text), relative_position + 70)
        ]

        command = f"/integrations show {service}"
        wants_config_detail = _INTEGRATION_CONFIG_DETAIL_RE.search(detail_window) is not None
        capability_only = _INTEGRATION_CAPABILITY_RE.search(window) is not None
        if (
            command not in seen_slash
            and _INTEGRATION_DETAIL_RE.search(window)
            and wants_config_detail
            and not capability_only
        ):
            planned.append(_slash_action(command, position))
            seen_slash.add(command)

    if planned:
        return planned

    provider_switch_action = _extract_llm_provider_switch(clause)
    if provider_switch_action is not None:
        planned.append(provider_switch_action)
        return planned

    synthetic_match = _SYNTHETIC_RDS_TEST_RE.search(clause.text)
    if synthetic_match is not None:
        planned.append(
            _synthetic_test_action("rds_postgres", clause.position + synthetic_match.start())
        )
        return planned

    sample_match = _SAMPLE_ALERT_RE.search(clause.text)
    if sample_match is not None:
        planned.append(_sample_alert_action("generic", clause.position + sample_match.start()))
        return planned

    shell_action = _extract_shell_command(clause)
    if shell_action is not None:
        planned.append(shell_action)

    return planned


def _extract_llm_provider_switch(clause: PromptClause) -> PlannedAction | None:
    if _LLM_PROVIDER_SWITCH_RE.search(clause.text) is None:
        return None

    provider_matches = list(_LLM_PROVIDER_RE.finditer(clause.text))
    if not provider_matches:
        return None

    target = provider_matches[-1]
    provider = target.group("provider").lower()
    return _llm_provider_action(provider, clause.position + target.start("provider"))


def _plan_actions_with_unhandled(message: str) -> tuple[list[PlannedAction], bool]:
    planned: list[PlannedAction] = []
    seen_slash: set[str] = set()
    has_unhandled_clause = False

    for clause in _split_prompt_clauses(message):
        clause_actions = _plan_clause_actions(
            clause,
            seen_slash=seen_slash,
        )
        if not clause_actions:
            has_unhandled_clause = True
        planned.extend(clause_actions)

    return sorted(planned, key=lambda action: action.position), has_unhandled_clause


def _plan_actions(message: str) -> list[PlannedAction]:
    actions, _has_unhandled_clause = _plan_actions_with_unhandled(message)
    return actions


def plan_cli_actions(message: str) -> list[str]:
    """Return safe read-only slash commands requested by a natural-language turn."""
    return [action.content for action in _plan_actions(message) if action.kind == "slash"]


def plan_terminal_tasks(message: str) -> list[str]:
    """Return a test-friendly view of all deterministic terminal tasks."""
    return [action.kind for action in _plan_actions(message)]


def _print_command_output(console: Console, output: str, *, style: str | None = None) -> None:
    if not output:
        return
    text = output.rstrip()
    console.print(Text(text) if style is None else Text(text, style=style))


def _print_planned_actions(console: Console, actions: list[PlannedAction]) -> None:
    console.print("[dim]Requested actions:[/dim]")
    for index, action in enumerate(actions, start=1):
        label = {
            "llm_provider": "LLM provider",
            "sample_alert": "sample alert",
            "shell": "shell",
            "slash": "command",
            "synthetic_test": "synthetic test",
        }[action.kind]
        console.print(
            f"[dim]{index}.[/dim] [{TERMINAL_ACCENT_BOLD}]{label}[/] {escape(action.content)}"
        )


def _terminate_child_process(proc: subprocess.Popen[Any]) -> None:
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


def _read_diag(buf: tempfile.SpooledTemporaryFile[bytes]) -> str:  # type: ignore[type-arg]
    """Read up to ``_SYNTHETIC_DIAG_CHARS`` bytes from a captured stderr buffer."""
    buf.seek(0)
    return buf.read(_SYNTHETIC_DIAG_CHARS).decode("utf-8", errors="replace").strip()


def _watch_synthetic_subprocess(
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    session: ReplSession,
    suite_name: str,
    stderr_buf: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
) -> None:
    def _history_text() -> str:
        return f"{suite_name} task:{task.task_id}"

    history_gen_when_watch_started = session.history_generation

    def _record_synthetic_if_current_session(ok: bool) -> None:
        if session.history_generation != history_gen_when_watch_started:
            return
        session.record("synthetic_test", _history_text(), ok=ok)

    def _run() -> None:
        started = time.monotonic()
        timed_out = False
        # Track whether *we* explicitly terminated the process so we can
        # distinguish a cancel-driven exit from a natural exit that happened
        # to race with a concurrent /cancel.
        terminated_by_watcher = False
        while proc.poll() is None:
            if time.monotonic() - started > _SYNTHETIC_TEST_TIMEOUT_SECONDS:
                timed_out = True
                task.request_cancel()
                _terminate_child_process(proc)
                terminated_by_watcher = True
                break
            if task.cancel_requested.is_set():
                _terminate_child_process(proc)
                terminated_by_watcher = True
                break
            time.sleep(_SYNTHETIC_POLL_SECONDS)

        try:
            if timed_out:
                task.mark_failed(f"timed out after {_SYNTHETIC_TEST_TIMEOUT_SECONDS}s")
                _record_synthetic_if_current_session(ok=False)
                return

            code = proc.returncode
            if code is None:
                task.mark_failed("subprocess did not report exit code")
                _record_synthetic_if_current_session(ok=False)
                return

            # Honour the real exit code when the process exited on its own.
            # Only treat as CANCELLED when *we* killed it after a cancel request;
            # a natural exit that races with /cancel should be recorded by its code.
            if terminated_by_watcher and task.cancel_requested.is_set():
                task.mark_cancelled()
                _record_synthetic_if_current_session(ok=False)
                return

            if code == 0:
                task.mark_completed(result="ok")
                _record_synthetic_if_current_session(ok=True)
            else:
                diag = _read_diag(stderr_buf)
                error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
                task.mark_failed(error_msg)
                _record_synthetic_if_current_session(ok=False)
        finally:
            stderr_buf.close()

    threading.Thread(target=_run, daemon=True, name=f"synthetic-{task.task_id}").start()


def _run_shell_command(command: str, session: ReplSession, console: Console) -> None:
    console.print(f"[bold]$ {escape(command)}[/bold]")
    parsed = parse_shell_command(command, is_windows=_IS_WINDOWS)
    decision = evaluate_policy(parsed=parsed)
    if not decision.allow:
        console.print(
            f"[yellow]command blocked:[/yellow] {escape(decision.reason or 'not allowed')}"
        )
        if decision.hint:
            console.print(f"[dim]{escape(decision.hint)}[/dim]")
        session.record("shell", command, ok=False)
        return

    argv_builtin = parsed.argv
    if argv_builtin is None and parsed.passthrough and parsed.command.strip():
        passthrough_body = parsed.command.strip()
        try:
            argv_builtin = shlex.split(passthrough_body, posix=not _IS_WINDOWS)
        except ValueError:
            try:
                argv_builtin = shlex.split(passthrough_body, posix=False)
            except ValueError:
                argv_builtin = None

    if argv_builtin is not None and argv_builtin[0].lower() == "cd":
        _run_cd_command(parsed.command, session, console)
        return
    if argv_builtin is not None and argv_builtin[0].lower() == "pwd":
        _run_pwd_command(parsed.command, session, console)
        return

    use_shell = parsed.passthrough
    if use_shell:
        console.print("[dim]explicit shell passthrough enabled[/dim]")

    try:
        result = execute_shell_command(
            command=parsed.command,
            argv=parsed.argv,
            use_shell=use_shell,
            timeout_seconds=_SHELL_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=_MAX_COMMAND_OUTPUT_CHARS,
        )
    except subprocess.TimeoutExpired:
        console.print(
            f"[red]command timed out after {_SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/red]"
        )
        session.record("shell", command, ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]command failed to start:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    _print_command_output(console, result.stdout)
    _print_command_output(console, result.stderr, style="red")
    ok = result.exit_code == 0
    if not ok:
        console.print(f"[red]exit code:[/red] {result.exit_code}")
    elif not result.stdout and not result.stderr:
        console.print("[dim]exit code: 0[/dim]")
    session.record("shell", command, ok=ok)


def _run_cd_command(command: str, session: ReplSession, console: Console) -> None:
    def _strip_outer_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    try:
        tokens = shlex.split(command, posix=not _IS_WINDOWS)
        if _IS_WINDOWS and len(tokens) > 1:
            tokens = [tokens[0], *(_strip_outer_quotes(token) for token in tokens[1:])]
    except ValueError as exc:
        console.print(f"[red]cd failed:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    if len(tokens) > 2:
        console.print("[red]cd failed:[/red] too many arguments")
        session.record("shell", command, ok=False)
        return

    target = Path(tokens[1]).expanduser() if len(tokens) == 2 else Path.home()
    try:
        os.chdir(target)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]cd failed:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)


def _run_pwd_command(command: str, session: ReplSession, console: Console) -> None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        console.print(f"[red]pwd failed:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    if len(tokens) != 1:
        console.print("[red]pwd failed:[/red] too many arguments")
        session.record("shell", command, ok=False)
        return

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)


def _run_sample_alert(template_name: str, session: ReplSession, console: Console) -> None:
    from app.cli.investigation import run_sample_alert_for_session

    console.print(f"[bold]sample alert:[/bold] {escape(template_name)}")
    task = session.task_registry.create(TaskKind.INVESTIGATION)
    task.mark_running()
    try:
        final_state = run_sample_alert_for_session(
            template_name=template_name,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[yellow]suggestion:[/yellow] {escape(exc.suggestion)}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        task.mark_failed(str(exc))
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", f"sample:{template_name}")


def _run_synthetic_test(suite_name: str, session: ReplSession, console: Console) -> None:
    if suite_name != "rds_postgres":
        console.print(f"[red]unknown synthetic suite:[/red] {escape(suite_name)}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    display_command = "opensre tests synthetic"
    console.print(f"[bold]$ {display_command}[/bold]")
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
    task.mark_running()
    # Lifetime managed by the watcher thread's finally block. noqa: SIM115
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg] # noqa: SIM115
        max_size=_SYNTHETIC_DIAG_CHARS * 2
    )
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is trusted interpreter path + module
            [sys.executable, "-m", "app.cli", "tests", "synthetic"],
            stdout=subprocess.DEVNULL,
            stderr=stderr_buf,
        )
    except Exception as exc:  # noqa: BLE001
        stderr_buf.close()
        task.mark_failed(str(exc))
        console.print(f"[red]synthetic test failed to start:[/red] {escape(str(exc))}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    task.attach_process(proc)
    _watch_synthetic_subprocess(task, proc, session, suite_name, stderr_buf)
    console.print(
        f"[dim]synthetic test started — task[/dim] [bold]{escape(task.task_id)}[/bold]. "
        f"[dim]/tasks[/dim] [dim]to monitor,[/dim] [bold]/cancel {escape(task.task_id)}[/bold] "
        f"[dim]to stop.[/dim]"
    )


def execute_cli_actions(message: str, session: ReplSession, console: Console) -> bool:
    """Execute inferred CLI and shell actions.

    Returns True when the message was handled. Unknown or ambiguous requests fall
    through to the LLM-backed assistant.
    """
    actions, has_unhandled_clause = _plan_actions_with_unhandled(message)
    if not actions:
        return False

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]assistant:[/]")
    _print_planned_actions(console, actions)
    console.print()
    console.print("[dim]Running requested actions:[/dim]")
    if not has_unhandled_clause:
        session.record("cli_agent", message)

    for action in actions:
        console.print()
        if action.kind == "slash":
            session.record("slash", action.content)
            console.print(f"[bold]$ {escape(action.content)}[/bold]")
            if not dispatch_slash(action.content, session, console):
                return True
        elif action.kind == "llm_provider":
            console.print(f"[bold]$ /model set {escape(action.content)}[/bold]")
            switch_llm_provider(action.content, console)
            session.record("slash", f"/model set {action.content}")
        elif action.kind == "shell":
            _run_shell_command(action.content, session, console)
        elif action.kind == "sample_alert":
            _run_sample_alert(action.content, session, console)
        else:
            _run_synthetic_test(action.content, session, console)

    console.print()
    return not has_unhandled_clause


__all__ = ["execute_cli_actions", "plan_cli_actions", "plan_terminal_tasks"]

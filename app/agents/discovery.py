"""Read-only discovery of local AI-agent processes."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.agents.registry import AgentRecord, AgentRegistry

logger = logging.getLogger(__name__)

_DEFAULT_CURSOR_PROJECTS_DIR = Path.home() / ".cursor" / "projects"
_PS_COMMAND = ("ps", "-axo", "pid=,args=")
_MAX_DISPLAY_COMMAND_LENGTH = 120
_NOISE_PROCESS_TOKENS: tuple[str, ...] = (
    "chrome_crashpad_handler",
    "shipit",
    "helper",
    "extension-host",
    "filewatcher",
    "pty-host",
    "shared-process",
    "language-server",
    "languageserver",
    "serverworker",
    "rust-analyzer",
    "esbuild",
)
_NOISE_ARG_PREFIXES: tuple[str, ...] = ("--type=", "--utility-sub-type=")
_LOOSE_AGENT_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("claude", "claude-code"),
    ("claude-code", "claude-code"),
    ("cursor", "cursor"),
    ("aider", "aider"),
    ("codex", "codex"),
    ("gemini", "gemini-cli"),
)


@dataclass(frozen=True)
class DiscoveredAgent:
    """Candidate process discovered for the ``opensre agents scan`` command."""

    name: str
    pid: int
    command: str

    def to_record(self) -> AgentRecord:
        return AgentRecord(name=self.name, pid=self.pid, command=self.command, source="discovered")


@dataclass(frozen=True)
class ProcessRow:
    """Minimal process-list row used by the discovery rules."""

    pid: int
    command: str


def discover_agent_processes(*, include_all: bool = False) -> list[DiscoveredAgent]:
    """Return likely local AI-agent sessions visible to the current user."""

    candidates: list[DiscoveredAgent] = []
    current_pid = os.getpid()
    for row in _current_process_rows():
        if row.pid <= 0 or row.pid == current_pid:
            continue
        cmdline = _split_command(row.command)
        process_name = _normalized_token(cmdline[0]) if cmdline else ""

        agent_name = _classify_agent(process_name, cmdline, include_all=include_all)
        if agent_name is None:
            continue
        candidates.append(
            DiscoveredAgent(name=f"{agent_name}-{row.pid}", pid=row.pid, command=row.command)
        )

    return sorted(candidates, key=lambda item: (item.name, item.pid))


def discover_agents(
    *,
    process_rows: Iterable[ProcessRow] | None = None,
    cursor_projects_dir: Path = _DEFAULT_CURSOR_PROJECTS_DIR,
) -> list[AgentRecord]:
    """Return agent-like processes discovered from the local machine.

    Discovery is intentionally read-only. The registry remains the source of
    explicit user-tracked agents; this function surfaces obvious Cursor,
    Claude Code, Codex, Aider, and Gemini CLI processes that are already
    running but have not been registered.
    """
    records_by_pid: dict[int, AgentRecord] = {}

    rows = list(process_rows) if process_rows is not None else _current_process_rows()
    for row in rows:
        name = _agent_name_for_command(row.command)
        if name is None:
            continue
        records_by_pid[row.pid] = AgentRecord(
            name=name,
            pid=row.pid,
            command=row.command,
            source="discovered",
        )

    for record in _discover_cursor_terminal_agents(cursor_projects_dir):
        records_by_pid.setdefault(record.pid, record)

    return sorted(records_by_pid.values(), key=lambda record: (record.name, record.pid))


def registered_and_discovered_agents(
    registry: AgentRegistry | None = None,
) -> list[AgentRecord]:
    """Merge explicit registry rows with read-only discovered agent rows."""
    registry = registry or AgentRegistry()
    records_by_pid = {record.pid: record for record in registry.list()}
    for record in discover_agents():
        records_by_pid.setdefault(record.pid, record)
    return sorted(records_by_pid.values(), key=lambda record: (record.name, record.pid))


def process_command(pid: int) -> str | None:
    """Best-effort command line for a PID, or ``None`` if unavailable."""

    try:
        proc = subprocess.run(
            ("ps", "-p", str(pid), "-o", "args="),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def display_command(command: str) -> str:
    """Return a terminal-friendly one-line command for scan output."""

    collapsed = " ".join(command.split())
    if len(collapsed) <= _MAX_DISPLAY_COMMAND_LENGTH:
        return collapsed
    return f"{collapsed[: _MAX_DISPLAY_COMMAND_LENGTH - 3]}..."


def _current_process_rows() -> list[ProcessRow]:
    try:
        proc = subprocess.run(
            _PS_COMMAND,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("agent discovery: failed to run ps", exc_info=True)
        return []
    if proc.returncode != 0:
        logger.debug("agent discovery: ps exited with code %s", proc.returncode)
        return []
    rows: list[ProcessRow] = []
    for line in proc.stdout.splitlines():
        row = _parse_ps_line(line)
        if row is not None:
            rows.append(row)
    return rows


def _parse_ps_line(line: str) -> ProcessRow | None:
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    return ProcessRow(pid=pid, command=parts[1])


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _discover_cursor_terminal_agents(cursor_projects_dir: Path) -> list[AgentRecord]:
    if not cursor_projects_dir.is_dir():
        return []

    records: list[AgentRecord] = []
    for path in sorted(cursor_projects_dir.glob("*/terminals/*.txt")):
        record = _record_from_cursor_terminal(path)
        if record is not None:
            records.append(record)
    return records


def _record_from_cursor_terminal(path: Path) -> AgentRecord | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:8]
    except OSError:
        return None

    metadata: dict[str, str] = {}
    for line in lines:
        if line == "---":
            continue
        key, sep, value = line.partition(":")
        if sep:
            metadata[key.strip()] = value.strip()

    raw_pid = metadata.get("pid")
    command = metadata.get("active_command") or metadata.get("last_command")
    if raw_pid is None or command is None:
        return None
    try:
        pid = int(raw_pid)
    except ValueError:
        return None

    name = _agent_name_for_command(command)
    if name is None:
        return None
    return AgentRecord(name=name, pid=pid, command=command, source="discovered")


def _agent_name_for_command(command: str) -> str | None:
    lower = command.lower()

    if ".cursor/extensions/anthropic.claude-code" in lower:
        return "cursor-claude-code"
    if "extension-host (agent-exec)" in lower:
        return "cursor-agent-exec"
    if "cursor-agent" in lower or "cursor agent" in lower:
        return "cursor-agent"
    if _has_command_token(lower, "claude") and _has_command_token(lower, "code"):
        return "claude-code"
    if _has_command_token(lower, "codex"):
        return "codex"
    if _has_command_token(lower, "aider"):
        return "aider"
    if _has_command_token(lower, "gemini"):
        return "gemini-cli"
    return None


def _classify_agent(process_name: str, cmdline: list[str], *, include_all: bool) -> str | None:
    if not cmdline:
        return None
    if _is_noise_process(process_name, cmdline):
        return _classify_agent_loose(process_name, cmdline) if include_all else None

    executable = _normalized_token(cmdline[0])
    args = [_normalized_token(part) for part in cmdline[1:]]
    lowered = [part.lower() for part in cmdline]

    if executable == "claude" and (
        "code" in args
        or _has_option_pair(lowered, "--input-format", "stream-json")
        or _has_option_pair(lowered, "--output-format", "stream-json")
    ):
        return "claude-code"
    if executable == "aider":
        return "aider"
    if executable == "codex":
        return "codex"
    if executable == "gemini":
        return "gemini-cli"
    if executable in {"cursor-agent", "cursor-agent-cli"}:
        return "cursor"
    if include_all:
        return _classify_agent_loose(process_name, cmdline)
    return None


def _classify_agent_loose(process_name: str, cmdline: list[str]) -> str | None:
    haystack = f"{process_name} {' '.join(cmdline)}".lower()
    tokens = {_normalized_token(part) for part in cmdline}
    tokens.add(_normalized_token(process_name))

    for signature, label in _LOOSE_AGENT_SIGNATURES:
        if signature in tokens or signature in haystack:
            return label
    return None


def _is_noise_process(process_name: str, cmdline: list[str]) -> bool:
    haystack_parts = [
        _normalized_token(process_name),
        *(_normalized_token(part) for part in cmdline),
    ]
    haystack = " ".join(part for part in haystack_parts if part)
    if any(token in haystack for token in _NOISE_PROCESS_TOKENS):
        return True
    return any(arg.startswith(_NOISE_ARG_PREFIXES) for arg in cmdline[1:])


def _has_option_pair(cmdline: list[str], option: str, value: str) -> bool:
    for index, part in enumerate(cmdline):
        if part == option and index + 1 < len(cmdline) and cmdline[index + 1] == value:
            return True
        if part == f"{option}={value}":
            return True
    return False


def _has_command_token(command: str, token: str) -> bool:
    normalized = command.replace("/", " ").replace("\\", " ")
    normalized = normalized.replace("-", " ").replace("_", " ")
    return token in normalized.split()


def _normalized_token(value: str) -> str:
    return Path(value.strip("'\"")).name.lower()


__all__ = [
    "DiscoveredAgent",
    "ProcessRow",
    "discover_agent_processes",
    "discover_agents",
    "display_command",
    "process_command",
    "registered_and_discovered_agents",
]

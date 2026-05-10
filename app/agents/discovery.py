"""Read-only discovery of local AI agent processes."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.agents.registry import AgentRecord, AgentRegistry

logger = logging.getLogger(__name__)

_DEFAULT_CURSOR_PROJECTS_DIR = Path.home() / ".cursor" / "projects"
_PS_COMMAND = ("ps", "-axo", "pid=,args=")


@dataclass(frozen=True)
class ProcessRow:
    """Minimal process-list row used by the discovery rules."""

    pid: int
    command: str


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


def _has_command_token(command: str, token: str) -> bool:
    normalized = command.replace("/", " ").replace("\\", " ")
    normalized = normalized.replace("-", " ").replace("_", " ")
    return token in normalized.split()


__all__ = [
    "ProcessRow",
    "discover_agents",
    "registered_and_discovered_agents",
]

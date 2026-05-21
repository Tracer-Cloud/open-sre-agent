"""Read-only discovery of local AI-agent processes."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.agents.probe import process_has_open_codex_rollout
from app.agents.provider_ids import provider_from_classified_name
from app.agents.registry import AgentRecord, AgentRegistry

logger = logging.getLogger(__name__)

_DEFAULT_CURSOR_PROJECTS_DIR = Path.home() / ".cursor" / "projects"
_PS_COMMAND = ("ps", "-axo", "pid=,ppid=,args=")
_MAX_DISPLAY_COMMAND_LENGTH = 120
_CODEX_LAUNCHER_TOKENS: frozenset[str] = frozenset({"codex", "codex.js", "codex.mjs", "codex.cjs"})
_NODE_EXECUTABLES: frozenset[str] = frozenset({"node", "nodejs"})
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
_CLAUDE_CODE_CLI_ARG_TOKENS: frozenset[str] = frozenset(
    {"--resume", "--prefill", "--print", "--continue", "-p", "-c", "-r"}
)
_CLAUDE_DESKTOP_PATH_HINTS: tuple[str, ...] = (
    "claude.app/contents/",
    "/claude-desktop/",
    "/snap/claude/",
    "/usr/lib/claude-desktop/",
    "/.mount_claude",
    "\\program files\\claude\\",
    "\\appdata\\local\\programs\\claude\\",
)


@dataclass(frozen=True)
class DiscoveredAgent:
    """Candidate process discovered for the ``opensre agents scan`` command."""

    name: str
    pid: int
    command: str

    def to_record(self) -> AgentRecord:
        return AgentRecord(
            name=self.name,
            pid=self.pid,
            command=self.command,
            source="discovered",
            provider=provider_from_classified_name(self.name),
        )


@dataclass(frozen=True)
class ProcessRow:
    """Minimal process-list row used by the discovery rules."""

    pid: int
    command: str
    ppid: int | None = None


def discover_agent_processes(*, include_all: bool = False) -> list[DiscoveredAgent]:
    """Return likely local AI-agent sessions visible to the current user."""

    candidates_by_pid: dict[int, tuple[str, ProcessRow]] = {}
    current_pid = os.getpid()
    rows = _current_process_rows()
    for row in rows:
        if row.pid <= 0 or row.pid == current_pid:
            continue
        cmdline = _split_command(row.command)
        process_name = _normalized_token(cmdline[0]) if cmdline else ""

        agent_name = _classify_agent(process_name, cmdline, include_all=include_all)
        if agent_name is None:
            continue
        candidates_by_pid[row.pid] = (agent_name, row)

    for pid in _codex_duplicate_pids_to_drop(
        rows,
        {pid for pid, (name, _) in candidates_by_pid.items() if name == "codex"},
    ):
        candidates_by_pid.pop(pid, None)

    candidates = [
        DiscoveredAgent(name=f"{agent_name}-{row.pid}", pid=row.pid, command=row.command)
        for agent_name, row in candidates_by_pid.values()
    ]
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
            provider=provider_from_classified_name(name),
        )

    for record in _discover_cursor_terminal_agents(cursor_projects_dir):
        records_by_pid.setdefault(record.pid, record)

    for pid in _codex_duplicate_pids_to_drop(
        rows,
        {pid for pid, record in records_by_pid.items() if record.name == "codex"},
    ):
        records_by_pid.pop(pid, None)

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
            encoding="utf-8",
            errors="replace",
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


def classify_command_provider(command: str) -> str | None:
    """Return the canonical provider id for ``command``, or ``None``.

    Strict argv[0]-only matching for claude / aider / gemini, plus
    Cursor-extension substring rules, plus ``_is_codex_cmdline`` so
    Node-launched Codex wrappers (``node /opt/codex.js`` and friends)
    classify correctly without re-introducing the loose token-scan
    false-positives that ``provider_from_command`` was tightened to
    avoid.
    """
    cmdline = _split_command(command)
    if not cmdline:
        return None
    executable = _normalized_token(cmdline[0])
    lower = command.lower()

    if ".cursor/extensions/anthropic.claude-code" in lower:
        return "claude-code"
    if "extension-host (agent-exec)" in lower:
        return "cursor"
    if "cursor-agent" in lower or "cursor agent" in lower:
        return "cursor"
    if executable in {"claude", "claude-code"}:
        return "claude-code"
    if executable == "aider":
        return "aider"
    if executable == "gemini":
        return "gemini-cli"
    if _is_codex_cmdline(cmdline):
        return "codex"
    return None


def _current_process_rows() -> list[ProcessRow]:
    try:
        proc = subprocess.run(
            _PS_COMMAND,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    parts = stripped.split(maxsplit=2)
    if len(parts) < 2:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None

    if len(parts) == 2:
        try:
            ppid = int(parts[1])
        except ValueError:
            ppid = None
        return ProcessRow(pid=pid, ppid=ppid, command="")

    try:
        ppid = int(parts[1])
    except ValueError:
        ppid = None
    return ProcessRow(pid=pid, ppid=ppid, command=parts[2])


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _codex_duplicate_pids_to_drop(rows: Iterable[ProcessRow], codex_pids: set[int]) -> set[int]:
    if len(codex_pids) < 2:
        return set()

    rows_by_pid = {row.pid: row for row in rows if row.pid in codex_pids}
    rollout_owner_cache: dict[int, bool] = {}
    pids_to_drop: set[int] = set()

    def owns_rollout(pid: int) -> bool:
        if pid not in rollout_owner_cache:
            rollout_owner_cache[pid] = process_has_open_codex_rollout(pid)
        return rollout_owner_cache[pid]

    for child in rows_by_pid.values():
        if child.ppid is None:
            continue
        parent = rows_by_pid.get(child.ppid)
        if parent is None or not _is_codex_wrapper_native_pair(parent, child):
            continue

        keep_pid = _preferred_codex_pair_pid(parent, child, owns_rollout)
        drop_pid = parent.pid if keep_pid == child.pid else child.pid
        pids_to_drop.add(drop_pid)

    return pids_to_drop


def _is_codex_wrapper_native_pair(parent: ProcessRow, child: ProcessRow) -> bool:
    return _is_node_codex_wrapper(parent.command) and _is_native_codex_process(child.command)


def _is_node_codex_wrapper(command: str) -> bool:
    cmdline = _split_command(command)
    return _is_node_codex_cmdline(cmdline)


def _is_native_codex_process(command: str) -> bool:
    cmdline = _split_command(command)
    return bool(cmdline) and _is_codex_launcher_token(cmdline[0])


def _is_codex_command(command: str) -> bool:
    return _is_codex_cmdline(_split_command(command))


def _is_codex_cmdline(cmdline: list[str]) -> bool:
    if not cmdline:
        return False
    if _is_codex_launcher_token(cmdline[0]):
        return True
    return _is_node_codex_cmdline(cmdline)


def _is_node_codex_cmdline(cmdline: list[str]) -> bool:
    if not cmdline or _normalized_token(cmdline[0]) not in _NODE_EXECUTABLES:
        return False
    return any(_is_codex_launcher_token(part) for part in cmdline[1:])


def _is_codex_launcher_token(value: str) -> bool:
    return _normalized_token(value) in _CODEX_LAUNCHER_TOKENS


def _preferred_codex_pair_pid(
    parent: ProcessRow,
    child: ProcessRow,
    owns_rollout: Callable[[int], bool],
) -> int:
    parent_owns_rollout = owns_rollout(parent.pid)
    child_owns_rollout = owns_rollout(child.pid)
    if parent_owns_rollout and not child_owns_rollout:
        return parent.pid
    return child.pid


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
    return AgentRecord(
        name=name,
        pid=pid,
        command=command,
        source="discovered",
        provider=provider_from_classified_name(name),
    )


def _agent_name_for_command(command: str) -> str | None:
    lower = command.lower()
    cmdline = _split_command(command)

    if ".cursor/extensions/anthropic.claude-code" in lower:
        return "cursor-claude-code"
    if "extension-host (agent-exec)" in lower:
        return "cursor-agent-exec"
    if "cursor-agent" in lower or "cursor agent" in lower:
        return "cursor-agent"
    if not _is_claude_desktop_artifact(cmdline) and _claude_code_cli_matches_cmdline(cmdline):
        return "claude-code"
    if _is_codex_command(command):
        return "codex"
    if _has_command_token(lower, "aider"):
        return "aider"
    if _has_command_token(lower, "gemini"):
        return "gemini-cli"
    return None


def _claude_code_cli_matches_cmdline(cmdline: list[str]) -> bool:
    if not cmdline:
        return False
    if _normalized_token(cmdline[0]) != "claude":
        return False
    lowered = [part.lower() for part in cmdline]
    args = [_normalized_token(part) for part in cmdline[1:]]
    if "code" in args:
        return True
    if _has_option_pair(lowered, "--input-format", "stream-json"):
        return True
    if _has_option_pair(lowered, "--output-format", "stream-json"):
        return True
    if any(_option_name(token) in _CLAUDE_CODE_CLI_ARG_TOKENS for token in lowered[1:]):
        return True
    return len(cmdline) == 1


def _option_name(arg: str) -> str:
    return arg.split("=", 1)[0]


def _classify_agent(
    process_name: str,
    cmdline: list[str],
    *,
    include_all: bool,
) -> str | None:
    if not cmdline:
        return None
    executable = _normalized_token(cmdline[0])

    if executable == "claude":
        if _is_claude_desktop_artifact(cmdline):
            return None
        if _claude_code_cli_matches_cmdline(cmdline):
            return "claude-code"
        # Intentional fall-through: an unrecognised `claude …` shape still
        # reaches the noise / loose-classification path below so that `--all`
        # surfaces it via the `claude` token signature. Do not add an early
        # `return None` here — it would silently break `opensre agents scan --all`.

    if _is_noise_process(process_name, cmdline):
        return _classify_agent_loose(process_name, cmdline) if include_all else None

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
    if _is_claude_desktop_artifact(cmdline):
        return None
    haystack = f"{process_name} {' '.join(cmdline)}".lower()
    tokens = {_normalized_token(part) for part in cmdline}
    tokens.add(_normalized_token(process_name))

    for signature, label in _LOOSE_AGENT_SIGNATURES:
        if label == "codex":
            if _is_codex_cmdline(cmdline):
                return label
            continue
        if signature in tokens or signature in haystack:
            return label
    return None


def _is_claude_desktop_artifact(cmdline: list[str]) -> bool:
    # Match desktop-bundle / installer hints against argv[0] only — never against
    # the full command. Prompt-bearing flags (e.g. `claude --print "inspect
    # /Applications/Claude.app/..."`) must not falsely trip the negative filter.
    #
    # The trailing-slash sentinel lets `/claude-desktop/` match both directories
    # (`/usr/lib/claude-desktop/...`) and end-of-string binaries
    # (`/.../bin/claude-desktop`) while rejecting `/.../my-claude-desktop-tools/`.
    if not cmdline:
        return False
    lowered = cmdline[0].lower() + "/"
    return any(hint in lowered for hint in _CLAUDE_DESKTOP_PATH_HINTS)


def _is_noise_process(process_name: str, cmdline: list[str]) -> bool:
    # Restrict the haystack to process_name + argv[0] so prompt-bearing flags
    # like `claude --print "...helper..."` do not falsely trip the noise filter.
    haystack_parts = [_normalized_token(process_name)]
    if cmdline:
        haystack_parts.append(_normalized_token(cmdline[0]))
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

"""Discover local AI-agent processes that can be tracked by ``opensre agents``."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.agents.registry import AgentRecord

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
)
_MAX_DISPLAY_COMMAND_LENGTH = 120


@dataclass(frozen=True)
class DiscoveredAgent:
    """Candidate process discovered from the local process table."""

    name: str
    pid: int
    command: str

    def to_record(self) -> AgentRecord:
        return AgentRecord(name=self.name, pid=self.pid, command=self.command)


def discover_agent_processes(*, include_all: bool = False) -> list[DiscoveredAgent]:
    """Return likely local AI-agent sessions visible to the current user."""

    candidates: list[DiscoveredAgent] = []
    current_pid = os.getpid()
    for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            if pid <= 0 or pid == current_pid:
                continue
            cmdline = _cmdline_from_info(info)
            command = _command_from_cmdline(cmdline, info)
            process_name = str(info.get("name") or "")
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, ValueError):
            continue

        agent_name = _classify_agent(process_name, cmdline, include_all=include_all)
        if agent_name is None:
            continue
        candidates.append(DiscoveredAgent(name=f"{agent_name}-{pid}", pid=pid, command=command))

    return sorted(candidates, key=lambda item: (item.name, item.pid))


def process_command(pid: int) -> str | None:
    """Best-effort command line for a PID, or ``None`` if unavailable."""

    try:
        proc = psutil.Process(pid)
        cmdline = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, ProcessLookupError):
        return None
    if cmdline:
        return " ".join(shlex.quote(part) for part in cmdline)
    try:
        return proc.name()
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return None


def display_command(command: str) -> str:
    """Return a terminal-friendly one-line command for scan output."""

    collapsed = " ".join(command.split())
    if len(collapsed) <= _MAX_DISPLAY_COMMAND_LENGTH:
        return collapsed
    return f"{collapsed[: _MAX_DISPLAY_COMMAND_LENGTH - 3]}..."


def _cmdline_from_info(info: dict[str, object]) -> list[str]:
    raw_cmdline = info.get("cmdline")
    if isinstance(raw_cmdline, list) and raw_cmdline:
        return [str(part) for part in raw_cmdline if str(part)]
    name = str(info.get("name") or "")
    return [name] if name else []


def _command_from_cmdline(cmdline: list[str], info: dict[str, object]) -> str:
    if cmdline:
        return " ".join(shlex.quote(part) for part in cmdline)
    return str(info.get("name") or "")


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


def _normalized_token(value: str) -> str:
    return Path(value.strip("'\"")).name.lower()


__all__ = ["DiscoveredAgent", "discover_agent_processes", "display_command", "process_command"]

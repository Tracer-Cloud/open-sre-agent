"""Policy helpers for deterministic interactive-shell command safety."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

CommandClassification = Literal["read_only", "mutating", "restricted", "unknown"]

_EXPLICIT_SHELL_PREFIX = "!"
_SHELL_OPERATOR_RE = re.compile(r"(^|\s)(\|\||&&|[|;<>]|>>|<<|2>)(\s|$)")
_INLINE_SUBSHELL_RE = re.compile(r"`|\$\(")

_RESTRICTED_COMMANDS = frozenset(
    {
        "sudo",
        "su",
        "doas",
        "mount",
        "umount",
        "mkfs",
        "shutdown",
        "reboot",
        "poweroff",
        "init",
        "systemctl",
        "service",
        "passwd",
        "useradd",
        "userdel",
        "usermod",
        "groupadd",
        "groupdel",
        "chown",
        "chmod",
        "chgrp",
        "kill",
        "killall",
        "pkill",
        "iptables",
        "ufw",
        "dd",
    }
)

_MUTATING_COMMANDS = frozenset(
    {
        "rm",
        "mv",
        "cp",
        "mkdir",
        "rmdir",
        "touch",
        "ln",
        "truncate",
        "sed",
        "awk",
        "tee",
        "xargs",
        "make",
        "pip",
        "pip3",
        "poetry",
        "npm",
        "pnpm",
        "yarn",
        "apt",
        "apt-get",
        "apk",
        "yum",
        "dnf",
        "brew",
        "docker",
        "kubectl",
        "helm",
        "terraform",
        "ansible",
        "git",
    }
)

_READ_ONLY_COMMANDS = frozenset(
    {
        "pwd",
        "cd",
        "ls",
        "dir",
        "cat",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "cut",
        "rg",
        "grep",
        "find",
        "which",
        "whereis",
        "echo",
        "printf",
        "env",
        "printenv",
        "date",
        "uname",
        "whoami",
        "id",
        "ps",
        "top",
        "df",
        "du",
        "history",
        "true",
        "false",
    }
)

_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"status", "log", "diff", "show", "branch", "remote", "rev-parse"}
)
_READ_ONLY_KUBECTL_SUBCOMMANDS = frozenset(
    {"get", "describe", "logs", "top", "version", "api-resources"}
)
_READ_ONLY_HELM_SUBCOMMANDS = frozenset(
    {"list", "status", "history", "get", "search", "show", "env"}
)

_READ_ONLY_AWS_PREFIXES = ("get", "list", "describe")


@dataclass(frozen=True)
class ParsedShellCommand:
    """Structured command parsing result."""

    command: str
    argv: list[str] | None
    passthrough: bool
    parse_error: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome from applying shell command safety policy."""

    allow: bool
    classification: CommandClassification
    reason: str | None
    hint: str | None


def parse_shell_command(command: str, *, is_windows: bool) -> ParsedShellCommand:
    """Parse command text and detect explicit passthrough prefix."""
    stripped = command.strip()
    if stripped.startswith(_EXPLICIT_SHELL_PREFIX):
        passthrough_command = stripped[len(_EXPLICIT_SHELL_PREFIX) :].strip()
        if not passthrough_command:
            return ParsedShellCommand(
                command="",
                argv=None,
                passthrough=True,
                parse_error="missing command after passthrough prefix (!).",
            )
        return ParsedShellCommand(
            command=passthrough_command,
            argv=None,
            passthrough=True,
        )

    if (
        _SHELL_OPERATOR_RE.search(stripped) is not None
        or _INLINE_SUBSHELL_RE.search(stripped) is not None
    ):
        return ParsedShellCommand(
            command=stripped,
            argv=None,
            passthrough=False,
            parse_error=(
                "shell operators and command substitution are blocked in safe mode. "
                "Use !<command> to run this intentionally."
            ),
        )

    try:
        argv = shlex.split(stripped, posix=not is_windows)
    except ValueError:
        try:
            argv = shlex.split(stripped, posix=False)
        except ValueError as exc:
            return ParsedShellCommand(
                command=stripped,
                argv=None,
                passthrough=False,
                parse_error=f"could not parse command: {exc}",
            )

    if not argv:
        return ParsedShellCommand(
            command=stripped,
            argv=None,
            passthrough=False,
            parse_error="empty command.",
        )

    return ParsedShellCommand(command=stripped, argv=argv, passthrough=False)


def classify_command(argv: list[str]) -> CommandClassification:
    """Classify command into read-only, mutating, restricted, or unknown."""
    command = argv[0].lower()

    if command in _RESTRICTED_COMMANDS:
        return "restricted"
    if command in _READ_ONLY_COMMANDS:
        return "read_only"

    if command == "git":
        subcommand = argv[1].lower() if len(argv) > 1 else ""
        return "read_only" if subcommand in _READ_ONLY_GIT_SUBCOMMANDS else "mutating"

    if command == "kubectl":
        subcommand = argv[1].lower() if len(argv) > 1 else ""
        return "read_only" if subcommand in _READ_ONLY_KUBECTL_SUBCOMMANDS else "mutating"

    if command == "helm":
        subcommand = argv[1].lower() if len(argv) > 1 else ""
        return "read_only" if subcommand in _READ_ONLY_HELM_SUBCOMMANDS else "mutating"

    if command == "aws":
        subcommand = argv[1].lower() if len(argv) > 1 else ""
        return "read_only" if subcommand.startswith(_READ_ONLY_AWS_PREFIXES) else "mutating"

    if command in _MUTATING_COMMANDS:
        return "mutating"

    return "unknown"


def evaluate_policy(*, parsed: ParsedShellCommand) -> PolicyDecision:
    """Allow read-only commands by default for inferred execution."""
    if parsed.parse_error is not None:
        return PolicyDecision(
            allow=False,
            classification="unknown",
            reason=parsed.parse_error,
            hint="Rewrite as a plain command or use !<command> for explicit shell passthrough.",
        )

    if parsed.passthrough:
        return PolicyDecision(
            allow=True,
            classification="unknown",
            reason=None,
            hint=None,
        )

    if parsed.argv is None:
        return PolicyDecision(
            allow=False,
            classification="unknown",
            reason="failed to parse command.",
            hint="Rewrite as a plain command or use !<command> for explicit shell passthrough.",
        )

    classification = classify_command(parsed.argv)
    if classification == "read_only":
        return PolicyDecision(
            allow=True,
            classification=classification,
            reason=None,
            hint=None,
        )

    if classification == "mutating":
        return PolicyDecision(
            allow=False,
            classification=classification,
            reason="mutating commands are blocked in safe mode.",
            hint=(
                "Use a read-only command, or run !<command> to explicitly "
                "opt into shell passthrough."
            ),
        )

    if classification == "restricted":
        return PolicyDecision(
            allow=False,
            classification=classification,
            reason="restricted command is not allowed from inferred execution.",
            hint="Run the command directly in your shell if you truly intend it.",
        )

    return PolicyDecision(
        allow=False,
        classification=classification,
        reason="command is not in the safe read-only allowlist.",
        hint="Use a known read-only command, or run !<command> for explicit passthrough.",
    )


__all__ = [
    "ParsedShellCommand",
    "PolicyDecision",
    "classify_command",
    "evaluate_policy",
    "parse_shell_command",
]

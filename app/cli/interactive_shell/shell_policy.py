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
        "sort",
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
        "terraform",
        "ansible",
    }
)

# Commands that can exec arbitrary child processes via flags (e.g. find -exec, env <cmd>).
# Allowing them defeats the mutating-command policy because the dangerous child process
# is spawned by the permitted parent — no shell involved, policy never sees it.
# Same risk applies to xargs — it stays in `_MUTATING_COMMANDS` (blocked by name).
# Users who genuinely need these can prefix with ! for explicit passthrough.
_EXEC_WRAPPER_COMMANDS = frozenset({"find", "env"})

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
        "uniq",
        "cut",
        "rg",
        "grep",
        "which",
        "whereis",
        "echo",
        "printf",
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

# AWS CLI: flags that consume the next argv token as a value.
_AWS_TWO_ARG_FLAGS = frozenset(
    {
        "--region",
        "--endpoint-url",
        "--profile",
        "--ca-bundle",
        "--cli-read-timeout",
        "--cli-connect-timeout",
        "--output",
        "--query",
        "--color",
    }
)

# AWS CLI: boolean / no-argument long flags (must not skip a following positional).
_AWS_BOOLEAN_FLAGS = frozenset(
    {
        "--no-sign-request",
        "--no-paginate",
        "--no-verify-ssl",
        "--debug",
        "--no-cli-pager",
    }
)


def _aws_token_looks_like_read_operation(tok: str) -> bool:
    """Match verbs like get-caller-identity / list-tables without false-positive prefixes."""
    if tok.startswith(
        ("describe-", "list-", "get-", "batch-get-", "batch-describe-", "history-"),
    ):
        return True
    # Require a hyphen for bare get/list/describe prefix (avoids tokens like getter).
    if tok.startswith(("describe", "list", "get")):
        return "-" in tok
    return False


def _aws_cli_positional_args(argv: list[str]) -> list[str]:
    """Extract likely positional tokens after stripping common global CLI flags."""
    out: list[str] = []
    index = 1
    limit = len(argv)
    while index < limit:
        token = argv[index]
        lower = token.lower()
        if lower.startswith("--") and "=" in lower:
            index += 1
            continue
        lower_name = lower.split("=", maxsplit=1)[0]
        if lower_name in _AWS_BOOLEAN_FLAGS:
            index += 1
            continue
        if lower_name in _AWS_TWO_ARG_FLAGS or (
            lower.startswith("--")
            and "=" not in lower
            and index + 1 < limit
            and not argv[index + 1].startswith("-")
        ):
            index += 2
            continue
        if lower.startswith("-") and lower != "--":
            skip_value = (
                len(lower) >= 2
                and lower[1] != "-"
                and len(lower) == 2
                and index + 1 < limit
                and not argv[index + 1].startswith("-")
            )
            index += 2 if skip_value else 1
            continue
        out.append(lower)
        index += 1
    return out


def _aws_cli_argv_is_read_only(argv: list[str]) -> bool:
    """Treat common read-only AWS CLI calls as allowed (verbs after optional global flags).

    Matches service-scoped invocations (`aws ec2 describe-instances`) as well as
    shorthand read paths (`aws s3 ls`).
    """
    positional = _aws_cli_positional_args(argv)
    if not positional:
        return False

    for tok in positional:
        if _aws_token_looks_like_read_operation(tok):
            return True

    for index in range(len(positional) - 1):
        if positional[index] == "s3" and positional[index + 1] in {"ls", "lsf"}:
            return True

    return False


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
    if command in _EXEC_WRAPPER_COMMANDS:
        return "mutating"
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
        return "read_only" if _aws_cli_argv_is_read_only(argv) else "mutating"

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
            reason="Not allowed for assistant-run shell.",
            hint="Run it in your terminal if needed.",
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

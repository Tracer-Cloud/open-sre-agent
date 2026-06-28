"""Shell command parsing for the interactive REPL.

Alpha mode: no command-safety policy
------------------------------------
While OpenSRE is in alpha we run **every** command the user or action agent
asks for. There is intentionally no allowlist, no read-only / mutating /
restricted classification, and no deny floor — guardrails are deliberately
omitted to keep developer velocity high. See
``docs/interactive-shell-action-policy.md`` for the rationale.

This module's only job is to turn command text into a shape the runner can
execute:

* explicit ``!`` passthrough → run the remainder through a shell,
* commands using shell operators / substitution → run through a shell,
* anything that fails to tokenize → hand the raw string to a shell,
* everything else → split into ``argv`` and run without a shell (which also lets
  the runner detect the ``cd`` / ``pwd`` REPL builtins so the working directory
  persists across turns).

The only non-execution outcome is a ``parse_error`` for genuinely empty input
(e.g. a bare ``!``). That is input validation, not a safety guardrail.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

_EXPLICIT_SHELL_PREFIX = "!"
_SHELL_OPERATOR_RE = re.compile(r"(^|\s)(\|\||&&|[|;<>]|>>|<<|2>)(\s|$)")
_INLINE_SUBSHELL_RE = re.compile(r"`|\$\(")


@dataclass(frozen=True)
class ParsedShellCommand:
    """Structured command parsing result.

    ``use_shell`` is True when the command must run through a real shell (explicit
    ``!`` passthrough, shell operators / substitution, or input that could not be
    tokenized). ``passthrough`` records only the explicit ``!`` prefix so the
    runner can surface the "shell passthrough" hint for it.
    """

    command: str
    argv: list[str] | None
    passthrough: bool
    use_shell: bool
    parse_error: str | None = None


def _split_argv(command: str, *, is_windows: bool) -> list[str] | None:
    try:
        return shlex.split(command, posix=not is_windows)
    except ValueError:
        try:
            return shlex.split(command, posix=False)
        except ValueError:
            return None


def parse_shell_command(command: str, *, is_windows: bool) -> ParsedShellCommand:
    """Parse command text into an executable shape (no safety policy applied)."""
    stripped = command.strip()

    if stripped.startswith(_EXPLICIT_SHELL_PREFIX):
        passthrough_command = stripped[len(_EXPLICIT_SHELL_PREFIX) :].strip()
        if not passthrough_command:
            return ParsedShellCommand(
                command="",
                argv=None,
                passthrough=True,
                use_shell=True,
                parse_error="missing command after passthrough prefix (!).",
            )
        return ParsedShellCommand(
            command=passthrough_command,
            argv=None,
            passthrough=True,
            use_shell=True,
        )

    if (
        _SHELL_OPERATOR_RE.search(stripped) is not None
        or _INLINE_SUBSHELL_RE.search(stripped) is not None
    ):
        # Operators / substitution need a real shell; alpha mode runs them.
        return ParsedShellCommand(
            command=stripped,
            argv=None,
            passthrough=False,
            use_shell=True,
        )

    argv = _split_argv(stripped, is_windows=is_windows)
    if argv is None:
        # Could not tokenize (e.g. unbalanced quotes). Hand the raw string to the
        # shell instead of blocking it.
        return ParsedShellCommand(
            command=stripped,
            argv=None,
            passthrough=False,
            use_shell=True,
        )

    if not argv:
        return ParsedShellCommand(
            command=stripped,
            argv=None,
            passthrough=False,
            use_shell=False,
            parse_error="empty command.",
        )

    if is_windows:

        def _strip_outer_quotes(value: str) -> str:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                return value[1:-1]
            return value

        argv = [_strip_outer_quotes(token) for token in argv]

    return ParsedShellCommand(
        command=stripped,
        argv=argv,
        passthrough=False,
        use_shell=False,
    )


def argv_for_repl_builtin_detection(
    *, parsed: ParsedShellCommand, is_windows: bool
) -> list[str] | None:
    """Argv tokens for detecting ``cd`` / ``pwd`` REPL builtins.

    Only the plain ``argv`` path and explicit ``!`` passthrough opt into builtin
    detection. Operator / substitution commands run wholesale through the shell,
    so they intentionally return ``None`` here (a leading ``cd`` in
    ``cd /tmp && ls`` must not be hijacked by the builtin handler).
    """
    if parsed.argv is not None:
        return parsed.argv
    if not parsed.passthrough or not parsed.command.strip():
        return None
    return _split_argv(parsed.command.strip(), is_windows=is_windows)


__all__ = [
    "ParsedShellCommand",
    "argv_for_repl_builtin_detection",
    "parse_shell_command",
]

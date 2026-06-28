"""Reference text for OpenSRE interactive-shell CLI answers."""

from __future__ import annotations

import logging
import time

import click

from interactive_shell.agent_shell.grounding.diagnostics import GroundingSource
from interactive_shell.agent_shell.grounding.models import CacheStats

_logger = logging.getLogger(__name__)

_MAX_REFERENCE_CHARS = 28_000

# Heuristic: truncated or failed reference output must not be cached or the
# assistant would keep an empty reference for the whole process.
_MIN_CACHEABLE_CLI_REFERENCE_CHARS = 80
_CLI_REFERENCE_SENTINEL = "=== opensre --help ==="


def _is_cacheable_cli_reference(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < _MIN_CACHEABLE_CLI_REFERENCE_CHARS:
        return False
    return _CLI_REFERENCE_SENTINEL in text


def _current_cli_signature() -> str:
    """Stable signature of the CLI command surface and interactive slash commands.

    Bumps cache when subcommands change, slash-command metadata changes, or the
    installed package version changes.
    """
    from cli.__main__ import cli
    from config.version import get_version
    from interactive_shell.command_registry import SLASH_COMMANDS

    cmd_names = ",".join(sorted(cli.commands.keys()))
    slash_names = ",".join(sorted(SLASH_COMMANDS.keys()))
    return f"opensre={get_version()}|commands={cmd_names}|slash={slash_names}"


def _format_param(param: click.Parameter) -> str:
    """Return a compact, side-effect-free description of a Click parameter."""
    if isinstance(param, click.Option):
        names = ", ".join((*param.opts, *param.secondary_opts))
        if not names:
            names = param.name or "(option)"
        value_hint = ""
        if not param.is_flag and not param.count:
            value_hint = " " + (param.metavar or param.name or "VALUE").upper()
        default = ""
        if param.show_default and param.default not in (None, "", ()):
            default = f" [default: {param.default}]"
        help_text = (param.help or "").strip()
        return f"  {names}{value_hint} - {help_text}{default}".rstrip()

    if isinstance(param, click.Argument):
        name = (param.name or "ARG").upper()
        required = "" if param.required else " (optional)"
        return f"  {name}{required}"

    return f"  {param.name or type(param).__name__}"


def _format_command_reference(
    command: click.Command,
    *,
    path: str,
    include_subcommands: bool = True,
) -> str:
    """Render Click command metadata without invoking help callbacks.

    ``click.Command.get_help()`` eventually calls ``format_help()``. The root
    OpenSRE group overrides that path to render via Rich's live console, which
    can leak into the interactive terminal when the assistant builds grounding
    context. This renderer inspects command objects directly instead.
    """
    lines = [f"Usage: {path} [OPTIONS]"]
    if isinstance(command, click.Group):
        lines[0] += " COMMAND [ARGS]..."
    elif command.params:
        arg_names = [
            (param.name or "ARG").upper()
            for param in command.params
            if isinstance(param, click.Argument)
        ]
        if arg_names:
            lines[0] += " " + " ".join(arg_names)

    help_text = (command.help or command.short_help or "").strip()
    if help_text:
        lines.extend(["", help_text])

    params = [param for param in command.params if not getattr(param, "hidden", False)]
    if params:
        lines.extend(["", "Options/Arguments:"])
        lines.extend(_format_param(param) for param in params)

    if include_subcommands and isinstance(command, click.Group):
        command_rows: list[tuple[str, str]] = []
        with click.Context(command, info_name=path.rsplit(" ", 1)[-1]) as ctx:
            for name in command.list_commands(ctx):
                subcommand = command.get_command(ctx, name)
                if subcommand is None or subcommand.hidden:
                    continue
                command_rows.append((name, subcommand.get_short_help_str(limit=160)))
        if command_rows:
            lines.extend(["", "Commands:"])
            lines.extend(f"  {name} - {summary}".rstrip() for name, summary in command_rows)

    return "\n".join(lines).rstrip() + "\n"


def _build_cli_reference_text_uncached() -> str:
    """Build a side-effect-free CLI reference without invoking Click commands."""
    from cli.__main__ import cli

    parts: list[str] = []

    parts.append("=== opensre --help ===\n")
    parts.append(_format_command_reference(cli, path="opensre"))

    with click.Context(cli, info_name="opensre") as ctx:
        for name in sorted(cli.commands.keys()):
            command = cli.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            parts.append(f"\n=== opensre {name} --help ===\n")
            parts.append(_format_command_reference(command, path=f"opensre {name}"))

    parts.append("\n=== Interactive-shell slash commands ===\n")
    parts.append(_interactive_shell_slash_hints())

    text = "".join(parts)
    if len(text) > _MAX_REFERENCE_CHARS:
        return text[:_MAX_REFERENCE_CHARS] + "\n\n[... reference truncated ...]\n"
    return text


def _interactive_shell_slash_hints() -> str:
    from interactive_shell.command_registry import SLASH_COMMANDS

    lines = [
        "In the interactive shell, describe an incident or paste alert JSON to run "
        + "a investigation pipeline, or chat with the terminal assistant for CLI help.",
        "Alpha mode runs every shell command with no guardrails: plain commands are parsed to "
        + "argv and run without a shell, while pipes, redirects, command substitution, and a "
        + "leading ! all run through a full shell. There is no read-only allowlist or blocked "
        + "command list.",
        "Slash commands:",
        "",
    ]
    for cmd in SLASH_COMMANDS.values():
        lines.append(f"  {cmd.name} - {cmd.description}")
    lines.extend(
        [
            "",
            "Non-interactive investigation: `opensre investigate` with stdin, file, or flags.",
            "Launch the interactive shell: `opensre` (requires a TTY).",
        ]
    )
    return "\n".join(lines)


class CliReference:
    """Session-scoped cache for assembled CLI help reference text.

    Holds its cache as instance state so each :class:`GroundingContext` (and
    thus each ``ReplSession``) owns an isolated cache with no module-level
    mutable globals.
    """

    name = "cli"

    def __init__(self) -> None:
        self._signature: str | None = None
        self._text: str | None = None
        self._created_at_monotonic: float = 0.0
        self._hits: int = 0
        self._misses: int = 0

    def build_text(self) -> str:
        """Assemble ``opensre`` and subcommand ``--help`` output for LLM grounding.

        Cached on this instance while the command registry signature matches.
        """
        sig = _current_cli_signature()
        if self._text is not None and self._signature == sig:
            self._hits += 1
            return self._text

        self._misses += 1
        text = _build_cli_reference_text_uncached()
        if _is_cacheable_cli_reference(text):
            self._signature = sig
            self._text = text
            self._created_at_monotonic = time.monotonic()
        else:
            self._signature = None
            self._text = None
            self._created_at_monotonic = 0.0
            _logger.warning(
                "CLI reference build produced non-cacheable output (%d chars); skipping cache",
                len(text),
            )
        return text

    def invalidate(self) -> None:
        """Drop cached CLI reference text (for tests or forced refresh)."""
        self._signature = None
        self._text = None
        self._created_at_monotonic = 0.0
        self._hits = 0
        self._misses = 0

    def stats(self) -> CacheStats:
        """Debug counters for grounding cache hit/miss and last signature."""
        return CacheStats(
            name=self.name,
            hits=self._hits,
            misses=self._misses,
            cached=self._text is not None,
            signature=self._signature,
            created_at_monotonic=self._created_at_monotonic,
        )

    def as_grounding_source(self) -> GroundingSource:
        return GroundingSource(name=self.name, stats_fn=self.stats)


__all__ = [
    "CliReference",
]

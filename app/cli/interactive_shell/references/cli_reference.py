"""Reference text for OpenSRE interactive-shell CLI answers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import click

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


@dataclass
class _CliReferenceCache:
    """Process-local cache for assembled CLI help reference text."""

    signature: str | None = None
    text: str | None = None
    created_at_monotonic: float = 0.0
    hits: int = 0
    misses: int = 0


_cli_reference_cache = _CliReferenceCache()


def _current_cli_signature() -> str:
    """Stable signature of the CLI command surface and interactive slash commands.

    Bumps cache when subcommands change, slash-command metadata changes, or the
    installed package version changes.
    """
    from app.cli.__main__ import cli
    from app.cli.interactive_shell.commands import SLASH_COMMANDS
    from app.version import get_version

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
    from app.cli.__main__ import cli

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
    from app.cli.interactive_shell.commands import SLASH_COMMANDS

    lines = [
        "In the interactive shell, describe an incident or paste alert JSON to run "
        + "a LangGraph investigation, or chat with the terminal assistant for CLI help.",
        "Deterministic shell commands are parsed to argv and run without a shell by default "
        + "(safe/read-only allowlist only). Pipes, redirects, command substitution, mutating "
        + "commands, and other cases need a leading ! for explicit full-shell passthrough.",
        "Slash commands:",
        "",
    ]
    for cmd in SLASH_COMMANDS.values():
        lines.append(f"  {cmd.name} - {cmd.help_text}")
    lines.extend(
        [
            "",
            "Non-interactive investigation: `opensre investigate` with stdin, file, or flags.",
            "Launch the interactive shell: `opensre` (requires a TTY).",
        ]
    )
    return "\n".join(lines)


def invalidate_cli_reference_cache() -> None:
    """Drop cached CLI reference text (for tests or forced refresh)."""
    _cli_reference_cache.signature = None
    _cli_reference_cache.text = None
    _cli_reference_cache.created_at_monotonic = 0.0
    _cli_reference_cache.hits = 0
    _cli_reference_cache.misses = 0


def get_cli_reference_cache_stats() -> dict[str, Any]:
    """Debug counters for grounding cache hit/miss and last signature."""
    return {
        "hits": _cli_reference_cache.hits,
        "misses": _cli_reference_cache.misses,
        "cached": _cli_reference_cache.text is not None,
        "signature": _cli_reference_cache.signature,
        "created_at_monotonic": _cli_reference_cache.created_at_monotonic,
    }


def build_cli_reference_text() -> str:
    """Assemble ``opensre`` and subcommand ``--help`` output for LLM grounding.

    Cached process-locally while the command registry signature matches.
    """
    sig = _current_cli_signature()
    if _cli_reference_cache.text is not None and _cli_reference_cache.signature == sig:
        _cli_reference_cache.hits += 1
        return _cli_reference_cache.text

    _cli_reference_cache.misses += 1
    text = _build_cli_reference_text_uncached()
    if _is_cacheable_cli_reference(text):
        _cli_reference_cache.signature = sig
        _cli_reference_cache.text = text
        _cli_reference_cache.created_at_monotonic = time.monotonic()
    else:
        _cli_reference_cache.signature = None
        _cli_reference_cache.text = None
        _cli_reference_cache.created_at_monotonic = 0.0
        _logger.warning(
            "CLI reference build produced non-cacheable output (%d chars); skipping cache",
            len(text),
        )
    return text


__all__ = [
    "build_cli_reference_text",
    "get_cli_reference_cache_stats",
    "invalidate_cli_reference_cache",
]

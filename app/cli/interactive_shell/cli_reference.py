"""Reference text for OpenSRE interactive-shell CLI answers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from click.testing import CliRunner

_logger = logging.getLogger(__name__)

_MAX_REFERENCE_CHARS = 28_000

# Heuristic: truncated or failed CliRunner output must not be cached or the
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


def _build_cli_reference_text_uncached() -> str:
    """Build reference via CliRunner without caching."""
    from app.cli.__main__ import cli

    runner = CliRunner()
    parts: list[str] = []

    def _invoke(args: list[str]) -> str:
        result = runner.invoke(cli, args, catch_exceptions=True)
        out = getattr(result, "output", "") or ""
        if result.exit_code != 0:
            _logger.warning("cli help invoke failed for %s: exit %s", args, result.exit_code)
        return out

    parts.append("=== opensre --help ===\n")
    parts.append(_invoke(["--help"]))

    for name in sorted(cli.commands.keys()):
        parts.append(f"\n=== opensre {name} --help ===\n")
        parts.append(_invoke([name, "--help"]))

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

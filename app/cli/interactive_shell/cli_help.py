"""LLM-grounded answers for procedural OpenSRE / CLI questions in the interactive shell."""

from __future__ import annotations

import logging

from click.testing import CliRunner
from rich.console import Console

from app.cli.interactive_shell.session import ReplSession

_logger = logging.getLogger(__name__)

_MAX_REFERENCE_CHARS = 28_000


def build_cli_reference_text() -> str:
    """Assemble ``opensre`` and subcommand ``--help`` output for LLM grounding."""
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
    parts.append(_repl_slash_hints())

    text = "".join(parts)
    if len(text) > _MAX_REFERENCE_CHARS:
        return text[:_MAX_REFERENCE_CHARS] + "\n\n[... reference truncated ...]\n"
    return text


def _repl_slash_hints() -> str:
    from app.cli.interactive_shell.commands import SLASH_COMMANDS

    lines = [
        "In the interactive shell, describe an incident or paste alert JSON to run "
        "a LangGraph investigation, or chat with the terminal assistant for CLI help.",
        "Slash commands:",
        "",
    ]
    for cmd in SLASH_COMMANDS.values():
        lines.append(f"  {cmd.name} — {cmd.help_text}")
    lines.extend(
        [
            "",
            "Non-interactive investigation: `opensre investigate` with stdin, file, or flags.",
            "Launch only the interactive shell: `opensre agent` (requires a TTY).",
        ]
    )
    return "\n".join(lines)


def answer_cli_help(question: str, session: ReplSession, console: Console) -> None:
    """Strict reference-only answer (same LLM path as :func:`answer_cli_agent`)."""
    from app.cli.interactive_shell.cli_agent import answer_cli_agent

    answer_cli_agent(question, session, console, grounding="reference_only")


__all__ = ["answer_cli_help", "build_cli_reference_text"]

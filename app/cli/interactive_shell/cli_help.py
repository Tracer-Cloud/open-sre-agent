"""LLM-grounded answers for procedural OpenSRE / CLI questions in the interactive shell."""

from __future__ import annotations

from rich.console import Console

from app.cli.interactive_shell.cli_reference import build_cli_reference_text
from app.cli.interactive_shell.session import ReplSession


def answer_cli_help(question: str, session: ReplSession, console: Console) -> None:
    """Strict reference-only answer (same LLM path as :func:`answer_cli_agent`)."""
    from app.cli.interactive_shell.cli_agent import answer_cli_agent

    answer_cli_agent(question, session, console, grounding="reference_only")


__all__ = ["answer_cli_help", "build_cli_reference_text"]

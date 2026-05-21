"""Documentation-aware procedural answers for the OpenSRE interactive shell.

When the router classifies an input as a procedural / how-to question we land
here. We retrieve the most relevant pages from the project ``docs/`` directory
and combine them with the CLI ``--help`` reference so the LLM answers from
maintained documentation rather than model memory.

The matching ``answer_cli_agent`` path remains available for free-form
terminal chat that may invoke runtime actions; this module is the strict
docs-grounded surface and never executes actions.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.prompt_logging import LlmRunInfo
from app.cli.interactive_shell.prompting.prompt_rules import (
    CLI_ASSISTANT_MARKDOWN_RULE,
    INTERACTIVE_SHELL_TERMINOLOGY_RULE,
)
from app.cli.interactive_shell.references.cli_reference import build_cli_reference_text
from app.cli.interactive_shell.references.docs_reference import build_docs_reference_text
from app.cli.interactive_shell.references.grounding_diagnostics import (
    log_grounding_cache_diagnostics,
)
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import DIM, ERROR, STREAM_LABEL_ASSISTANT, stream_to_console
from app.cli.support.exception_reporting import report_exception

# Match the cli_agent terminology / formatting rules so docs answers feel
# consistent with the rest of the interactive shell.
_TERMINOLOGY_RULE = INTERACTIVE_SHELL_TERMINOLOGY_RULE
_MARKDOWN_RULE = CLI_ASSISTANT_MARKDOWN_RULE


def _build_grounded_prompt(question: str, cli_reference: str, docs_reference: str) -> str:
    """Build the system + user prompt for one docs-aware answer.

    Split out so tests can assert on grounding rules without invoking an LLM.
    """
    if docs_reference:
        docs_block = (
            "Use the docs reference below as the authoritative source for "
            "configuration, integration setup, deployment, and feature "
            "questions. If the docs do not cover the user's question, say "
            "so explicitly and suggest the closest relevant page, "
            "`opensre --help`, or `/help` inside the interactive shell. "
            "Do NOT invent setup steps that are not in the docs."
        )
        reference_block = (
            f"--- Project documentation ---\n{docs_reference}\n\n"
            f"--- CLI reference ---\n{cli_reference}\n"
        )
    else:
        docs_block = (
            "Project documentation is not available in this environment. "
            "Answer only from the CLI reference below; if it does not cover "
            "the question, say so and point the user to "
            "https://www.opensre.com/docs."
        )
        reference_block = f"--- CLI reference ---\n{cli_reference}\n"

    system = (
        "You are the OpenSRE documentation-aware CLI assistant. The user is "
        "in the OpenSRE interactive shell and is asking how to use, "
        "configure, install, deploy, or troubleshoot OpenSRE.\n"
        f"{docs_block}\n"
        "Prefer copy-pastable commands. Cite the doc page name in parentheses "
        "when an answer comes from the docs (e.g. '(see docs/datadog)'). "
        "Keep the answer focused and avoid unsupported instructions.\n\n"
        f"{_TERMINOLOGY_RULE}\n{_MARKDOWN_RULE}\n\n"
        f"{reference_block}"
    )
    user_block = f"--- Question ---\n{question}"
    return f"{system}\n{user_block}"


def answer_cli_help(
    question: str,
    _session: ReplSession,
    console: Console,
) -> LlmRunInfo | None:
    """Run one turn of the documentation-aware procedural assistant.

    Pulls the top-N relevant docs pages for ``question``, combines them with
    the CLI reference, and asks the reasoning model to answer strictly from
    the assembled grounding. Behaves as a no-op for the session's action
    history (stateless across turns) so it never interferes with follow-up
    routing on a prior investigation.

    ``_session`` is accepted for API symmetry with :func:`answer_cli_agent` and
    input routing; this path does not read session state today.
    """
    try:
        from app.services.llm_client import get_llm_for_reasoning
    except Exception as exc:
        report_exception(exc, context="interactive_shell.cli_help.import")
        console.print(f"[{ERROR}]LLM client unavailable:[/] {escape(str(exc))}")
        return None

    cli_reference = build_cli_reference_text()
    docs_reference = build_docs_reference_text(question)
    log_grounding_cache_diagnostics("cli_help_grounding")
    prompt = _build_grounded_prompt(question, cli_reference, docs_reference)

    try:
        client = get_llm_for_reasoning()
        started = time.monotonic()
        response_text = stream_to_console(
            console,
            label=STREAM_LABEL_ASSISTANT,
            chunks=client.invoke_stream(prompt),
        )
    except KeyboardInterrupt:
        console.print(f"[{DIM}]· cancelled[/]")
        return None
    except Exception as exc:
        report_exception(exc, context="interactive_shell.cli_help.stream")
        console.print(f"[{ERROR}]assistant failed:[/] {escape(str(exc))}")
        return None
    return LlmRunInfo(
        model=_resolve_model_name(client),
        provider=_resolve_provider_name(client),
        latency_ms=int((time.monotonic() - started) * 1000),
        response_text=response_text,
    )


def _resolve_model_name(client: object) -> str | None:
    value = getattr(client, "_model", None)
    return value if isinstance(value, str) and value else None


def _resolve_provider_name(client: object) -> str | None:
    provider_label = getattr(client, "_provider_label", None)
    if isinstance(provider_label, str) and provider_label:
        return provider_label.strip().lower().replace(" ", "_")
    name = type(client).__name__.lower()
    if "openai" in name:
        return "openai"
    if "bedrock" in name:
        return "bedrock"
    if "cli" in name:
        return "cli"
    if "anthropic" in name or "llmclient" in name:
        return "anthropic"
    return None


__all__ = ["answer_cli_help"]

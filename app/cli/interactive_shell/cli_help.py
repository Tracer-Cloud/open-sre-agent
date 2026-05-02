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

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from app.cli.interactive_shell.cli_reference import build_cli_reference_text
from app.cli.interactive_shell.docs_reference import build_docs_reference_text
from app.cli.interactive_shell.loaders import llm_loader
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD

# Match the cli_agent terminology / formatting rules so docs answers feel
# consistent with the rest of the interactive shell.
_TERMINOLOGY_RULE = (
    "Terminology: always call this surface the 'interactive shell' (the "
    "OpenSRE interactive terminal launched via `opensre` or `opensre agent`). "
    "Never use the word 'REPL' in user-facing answers - it is internal jargon."
)

_MARKDOWN_RULE = (
    "Formatting: respond in concise Markdown. Markdown will be rendered "
    "in the user's terminal, so tables, **bold**, lists, and `code spans` "
    "will display correctly - do not wrap the whole answer in a code fence."
)


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


def answer_cli_help(question: str, session: ReplSession, console: Console) -> None:  # noqa: ARG001
    """Run one turn of the documentation-aware procedural assistant.

    Pulls the top-N relevant docs pages for ``question``, combines them with
    the CLI reference, and asks the reasoning model to answer strictly from
    the assembled grounding. Behaves as a no-op for the session's action
    history (stateless across turns) so it never interferes with follow-up
    routing on a prior investigation.
    """
    try:
        from app.services.llm_client import get_llm_for_reasoning
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]LLM client unavailable:[/red] {escape(str(exc))}")
        return

    cli_reference = build_cli_reference_text()
    docs_reference = build_docs_reference_text(question)
    prompt = _build_grounded_prompt(question, cli_reference, docs_reference)

    try:
        with llm_loader(console):
            client = get_llm_for_reasoning()
            response = client.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]assistant failed:[/red] {escape(str(exc))}")
        return

    text = getattr(response, "content", None) or str(response)
    text_str = str(text)

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]assistant:[/]")
    console.print(Markdown(text_str))
    console.print()


__all__ = [
    "answer_cli_help",
    "build_cli_reference_text",
    "build_docs_reference_text",
]

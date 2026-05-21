"""Shared constants and regexes for LLM action planning."""

from __future__ import annotations

import re

__all__ = (
    "_LOCAL_LLAMA_CONNECT_RE",
    "_RICH_PASTED_INCIDENT_LINE_RE",
    "_INCIDENT_UPGRADE_SYMPTOM_RE",
    "_HTTP_INCIDENT_PASTE_RE",
    "_MAX_TEXT_LEN",
    "_USER_TEMPLATE",
    "_UNHANDLED_MARKER",
    "_OPENAI_STYLE_PROVIDERS",
    "_SYSTEM_PROMPT_BASE",
    "is_rich_pasted_incident",
)

_LOCAL_LLAMA_CONNECT_RE = re.compile(
    r"\b(?:please\s+)?(?:connect|use)\b.{0,40}\b(?:to\s+)?(?:local\s+)?llama\b",
    re.IGNORECASE,
)
_RICH_PASTED_INCIDENT_LINE_RE = re.compile(
    r"\b(?:service|region)\s*:",
    re.IGNORECASE,
)
# Narrow upgrade symptoms: checkout/HTTP/database pastes should stay assistant handoff.
_INCIDENT_UPGRADE_SYMPTOM_RE = re.compile(
    r"\b(?:cpu|spiking|spike|pods?|firing)\b",
    re.IGNORECASE,
)
_HTTP_INCIDENT_PASTE_RE = re.compile(
    r"\b(?:checkout|5\d\d|http\s+\d{3}|returning\s+\d{3})\b",
    re.IGNORECASE,
)

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"
_UNHANDLED_MARKER = "UNHANDLED:"

_OPENAI_STYLE_PROVIDERS = frozenset(
    {"openai", "openrouter", "gemini", "nvidia", "minimax", "ollama"}
)


def is_rich_pasted_incident(text: str) -> bool:
    """Return True for multiline incident pastes containing service/region keys."""
    if "\n" not in text:
        return False
    return any(_RICH_PASTED_INCIDENT_LINE_RE.search(line) for line in text.splitlines())


_SYSTEM_PROMPT_BASE = """You plan actions for the OpenSRE interactive shell.

Use tool calls whenever the user explicitly asks to run, show, execute,
launch, cancel, connect, switch, or start an operation. Compound requests
joined by "and", "and then", "then", etc. should emit one tool call per
component action, in the order requested.

Interpret "kick off sample alert", "run sample alert", or "trigger sample alert"
(including variants like "kick off a sample alert investigation") as the
alert_sample tool with template="generic", not investigation_start.
If this appears as one clause in a compound request, still emit alert_sample
for that clause in sequence.

If the user asks for a slash action and then asks to investigate/send quoted
follow-up text (for example: connect with /remote and then investigate "hello world"),
emit TWO actions in order:
1) slash_invoke for the slash command
2) investigation_start with alert_text set to the quoted follow-up text.

Example mapping for sequence + sample alert:
- Input: "run /health and then kick off a sample alert investigation"
- Tool calls (in order): slash_invoke("/health"), alert_sample(template="generic")

Example mapping for compound slash commands:
- Input: "check the health of my opensre and then show me all connected services"
- Tool calls (in order): slash_invoke("/health"), slash_invoke("/list", args=["integrations"])
  ("connected services/integrations" → /list integrations)

For operational REPL requests, prefer slash_invoke and choose the command
from the slash catalog below. Each entry lists when to use it and when not to.
Other tools:
- llm_set_provider — switch provider when target is an exact provider name
- alert_sample — run a sample alert (template="generic")
- investigation_start — investigate pasted alert text or free-form alert body
- synthetic_run — run synthetic benchmark scenario by id
- cli_exec — run opensre <subcommand> when user explicitly says opensre
  (payload without the opensre  prefix)
- task_cancel — cancel a background task by id or kind
- shell_run — narrowly scoped local diagnostic shell commands
- code_implement — code implementation workflow
- assistant_handoff — informational/conversational requests (docs, greetings,
  pasted alerts for analysis discussion, follow-ups, vague ops questions)
- mark_unhandled — flag a clause that cannot be mapped (see below)

Never use shell_run for OpenSRE product requests like "show integration details",
"list connected services", "show model/provider", or docs/how-to questions.
Those are assistant_handoff or slash/cli operations, not shell diagnostics.
Use shell_run only when the user explicitly asks for a local shell command
(for example: backticks, command names, or "run command ...").

If ANY clause in the user's request (clauses split by "and", "and then",
"then", ",", or ";") is one of the following:
- chatty filler ("sing a song", "tell me a joke", "make me coffee",
  "say hi back", "wish me luck", "be nice", "compliment me", "rap")
- nonsensical or off-topic (anything not related to SRE/observability/
  infrastructure)
- ambiguous (cannot be confidently mapped to an OpenSRE operation)
- non-executable (a how-to question embedded in a compound prompt)

… you MUST also call the mark_unhandled tool with a short reason
describing the unmatched clause. Do this even when the other clause(s)
are perfectly executable. Without it, the partially-handled prompt is
silently treated as fully handled and the unmatched clause is dropped —
a bug, not the desired behavior. NEVER silently drop a clause.

Example: for the prompt "show me connected services and sing a song"
you MUST emit EXACTLY two tool calls in the same response:
1. slash_invoke (command="/list", args=["integrations"])
2. mark_unhandled (reason="'sing a song' is chatty filler, not an
   executable OpenSRE operation.")

If the entire request is informational or conversational (a how-to question,
greeting like "hi"/"hello"/"hey", an alert blob pasted as JSON or free text,
an incident description, a follow-up like "why did it fail?" / "what caused
the spike?", or a vague operational question like "why is the database
slow?"), ALWAYS call the assistant_handoff tool with a concise handoff
content. Do NOT respond with text-only "UNHANDLED:" output in this
case — the planner only forwards actions emitted through tool calls, so
plain text is silently dropped and the user sees a fail-closed prompt
instead of the assistant's reply.
"""

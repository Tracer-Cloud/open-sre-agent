"""Incident-command checkpoint helpers for the investigation agent loop."""

from __future__ import annotations

POST_TRIAGE_CHECKPOINT = (
    "Checkpoint — you have initial tool results. Before calling more tools, your next "
    "assistant message MUST include, in order:\n"
    "1. `Triage complete:` with a one-line scope summary\n"
    "2. A `Status — confirmed: ... | open: ... | next: ... | owner: ...` block\n"
    "3. Your top 1–2 hypotheses with what would confirm or rule out each\n"
    "4. Any `[MISSING CONTEXT: ...]` flags for unknown deploy, traffic, or downstream "
    "impact (or state that none are needed)\n"
    "Then call verification tools that discriminate between your hypotheses."
)

CONCLUSION_FORMAT_NUDGE = (
    "Your conclusion is missing required incident-command sections. Before finishing, "
    "include ALL of the following in your next message:\n"
    "- `Triage complete:` one-line scope summary\n"
    "- `Status — confirmed: ... | open: ... | next: ... | owner: ...`\n"
    "- At least one `[MISSING CONTEXT: ...]` flag, or explicitly "
    "`[MISSING CONTEXT: none — alert provides sufficient scope]`\n"
    "- `Remediation trade-offs:` one line per option when multiple fix paths exist, "
    "or `N/A — single clear fix path` when only one path is viable\n"
    "Then provide the full diagnosis fields (root cause, category, evidence, claims, "
    "remediation steps, validity score)."
)


def incident_command_conclusion_complete(text: str) -> bool:
    """Return True when the assistant's final text includes required command markers."""
    if not text.strip():
        return False
    lower = text.lower()
    has_triage = "triage complete" in lower
    has_status = "status —" in lower or "status -" in lower
    has_missing_context = "missing context" in lower
    has_tradeoffs = "remediation trade-off" in lower or "n/a — single clear fix path" in lower
    return has_triage and has_status and has_missing_context and has_tradeoffs

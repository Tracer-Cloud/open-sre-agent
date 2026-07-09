"""Incident-command checkpoint helpers for the investigation agent loop."""

from __future__ import annotations

POST_TRIAGE_CHECKPOINT = (
    "Checkpoint — you have initial tool results. Before calling more tools, your next "
    "assistant message MUST include, in order:\n"
    "1. `Triage complete:` with a one-line scope summary\n"
    "2. A `Status — confirmed: ... | open: ... | next: ... | owner: ...` block\n"
    "3. `Hypotheses:` with your top 1–2 hypotheses; for each, state what would confirm "
    "or rule it out\n"
    "4. `Follow-up questions:` with at least one direct question for responders "
    "(e.g. recent deploy, traffic change, downstream impact), or "
    "`Follow-up questions: none — alert provides sufficient scope`\n"
    "Then call verification tools that discriminate between your hypotheses."
)

CONCLUSION_FORMAT_NUDGE = (
    "Your conclusion is missing required incident-command sections. Before finishing, "
    "include ALL of the following in your next message:\n"
    "- `Triage complete:` one-line scope summary\n"
    "- `Status — confirmed: ... | open: ... | next: ... | owner: ...`\n"
    "- `Hypotheses:` top 1–2 hypotheses with confirm/rule-out criteria for each\n"
    "- `Verification:` which tools tested which hypothesis and what they showed\n"
    "- `Follow-up questions:` at least one direct question for responders (ending with ?), "
    "or `Follow-up questions: none — alert provides sufficient scope`\n"
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
    has_hypotheses = "hypotheses:" in lower
    has_verification = "verification:" in lower
    has_follow_up_questions = "follow-up questions:" in lower or "follow-up question:" in lower
    has_tradeoffs = "remediation trade-off" in lower or "n/a — single clear fix path" in lower
    return (
        has_triage
        and has_status
        and has_hypotheses
        and has_verification
        and has_follow_up_questions
        and has_tradeoffs
    )

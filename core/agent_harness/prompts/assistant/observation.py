"""Per-turn user-half blocks: tool observation and action-planner handoffs."""

from __future__ import annotations

HANDOFF_GUIDANCE: dict[str, str] = {
    "provider:local_llama_connect": (
        "The action planner handed off a vague local-model connection request. "
        '"Local llama" is not an exact provider name. Answer with setup guidance:\n'
        "- For first-time setup, recommend `opensre onboard local_llm` or "
        "`/onboard local_llm` (installs and configures Ollama locally).\n"
        "- After Ollama is installed, mention `/model set ollama` to switch the "
        "active provider.\n"
        "- Do NOT suggest `/integrations setup llama`, `/remote`, or claim you "
        "switched providers.\n\n"
    ),
    "follow_up:prior_investigation": (
        "The action planner handed off a retrospective question about the "
        "investigation already completed in this session. Answer it from the "
        "'--- Prior investigation in this session ---' section: lead with the "
        "root cause and the findings it records. Do NOT ask which incident they "
        "mean, do NOT ask for alert context, and do NOT suggest starting a new "
        "investigation.\n\n"
    ),
    # Metric/read ask with the authoritative integration missing.
    # Prefix key: ``build_handoff_guidance_block`` matches ``evidence_tier:L0_degraded:…``.
    "evidence_tier:L0_degraded": (
        "The turn's authoritative live source is NOT connected in this session "
        "(evidence tier L0_degraded; the service id is the suffix after "
        "`evidence_tier:L0_degraded:`). Finish a useful answer without live "
        "data — do not stall on discovery or empty tool lists.\n"
        "Structure the reply like this:\n"
        "1. One plain sentence: you cannot return a live count because that "
        "source is not connected (name it).\n"
        "2. How to measure once connected (e.g. confirm the OS property name "
        "in the schema — `$os` vs `$os_name`).\n"
        "3. A short draft query in a fenced code block, clearly labeled as a "
        "draft to verify after connect — never invent metric numbers as fact.\n"
        "Never claim the source is connected, never imply a live query already "
        "ran. Do NOT offer a full incident investigation. Do NOT close with "
        "**Want me to:** (no live query offer, no investigation offer). "
        "Do NOT thrash on empty tool listings. The harness appends one "
        "integration upgrade CTA after your reply — do not duplicate that CTA "
        "and do not open an onboarding wizard unprompted. A bare user yes after "
        "that CTA will run the connect slash; do not invent a second Want-me-to.\n\n"
    ),
    "evidence_tier:metric_unformed": (
        "Gather ran but did not execute a live metric query (schema/list probes "
        "only, unknown event, or the query could not be formed). Do not invent "
        "a count.\n"
        "Structure the reply like this:\n"
        "1. One plain sentence: the live query could not be formed and why.\n"
        "2. A short draft query in a fenced code block, labeled as a draft, "
        "in the query language of the preferred connected analytics source. "
        "Never invent metric numbers as fact.\n"
        "3. Exactly one setup line using a valid `/integrations setup <id>` "
        "(preferred source id from this session). Do not invent a vendor.\n"
        "Do NOT offer a full incident investigation. Do NOT close with "
        "**Want me to:**. Stop after this reply.\n\n"
    ),
    # Connected preferred source failed auth/config after gather.
    # Prefix: ``evidence_tier:L0_degraded:config:<ids>`` (matched before plain L0).
    "evidence_tier:L0_degraded:config": (
        "The turn's authoritative live source IS registered in this session, "
        "but gather failed because of credentials or configuration "
        "(evidence tier L0_degraded config; service ids follow "
        "`evidence_tier:L0_degraded:config:`). Be honest about the failure — "
        "do not invent a live count.\n"
        "Structure the reply like this:\n"
        "1. One plain sentence: the named source failed auth/config (quote "
        "the error briefly if present in the tool results).\n"
        "2. How to measure once credentials work (property names / draft "
        "query labeled as draft — never invent metric numbers as fact).\n"
        "Do NOT claim the query succeeded. Do NOT offer a full incident "
        "investigation. Do NOT close with **Want me to:**. The harness "
        "appends one reconnect/setup CTA after your reply — do not duplicate "
        "it and do not open an onboarding wizard unprompted.\n\n"
    ),
    # SessionGoal checklist progress (host loop / continuation nudges).
    "session_goal:": (
        "An session goal is active. When you finish a checklist item, "
        "include the structured tag `session_goal:done=<0-based-index>` "
        "(comma-separate multiple). When every item is done, include "
        "`session_goal:achieved`. Put those tags on their own at the end of "
        "the reply; the harness strips them before the user sees the text. "
        "Do not ask whether to continue while the goal is active. "
        "Do NOT close with **Want me to:** (no investigation offer, no "
        "follow-up menu) — the session-goal loop owns continuation.\n\n"
    ),
    # Prefix key: ``build_handoff_guidance_block`` matches any
    # ``database_query:<topic>`` tag (mysql_active_connections, mariadb_dashboard, …).
    "database_query:": (
        "The action planner handed off a named database or tool query (MySQL, "
        "MariaDB, etc.). Name the database/tool the user asked about in your answer "
        "(do not refer to it only as 'that query'). If it is not connected in this "
        "session, explain how to connect it: `/mcp connect <server>` for MCP "
        "database tools, or `/integrations setup <service>` when a first-party "
        "integration exists. Do NOT offer a full incident investigation for a "
        "read-only connection or query request. Do NOT answer with only a generic "
        "'no integrations' line that omits the named database/tool.\n\n"
    ),
    # Prefix key: bare incident / symptom statements (oracle 325 family).
    "incident_description:": (
        "The action planner handed off a bare incident or symptom description "
        "(no explicit investigate verb). In your reply, name the user's stated "
        "service/component and any error codes or rates they gave (for example "
        "checkout, 502, 30%) before asking for more context or offering a full "
        "investigation. Do not paraphrase the incident into a generic "
        "'production error pattern' that drops those specifics. With little or "
        "no connected evidence, still acknowledge the reported symptoms, say "
        "what you would check next, and close with **Want me to:** run a full "
        "investigation — do not claim you cannot help.\n\n"
    ),
}

# Prefix keys in ``HANDOFF_GUIDANCE`` (trailing ``:``) match any tag with that prefix.
# Longer ``L0_degraded:config`` must precede ``L0_degraded`` so config tags win.
_HANDOFF_GUIDANCE_PREFIXES: tuple[str, ...] = (
    "evidence_tier:L0_degraded:config",
    "evidence_tier:L0_degraded",
    "evidence_tier:metric_unformed",
    "session_goal:",
    "database_query:",
    "incident_description:",
)

_ON_SCREEN_FRAMING = (
    "A read-only discovery command was just run to answer the user's question; "
    "its output is below. Summarize it to answer the user's question directly, "
    "citing the relevant status. The output is already on screen, so keep "
    "**Here's what that looks like:** brief or omit it when it would repeat "
    "what the user just saw. Still end with **Want me to:** and a specific "
    "next step tied to the finding (for integration questions: connect another "
    "integration, verify a failed service, or set up a missing one)."
)

_ON_SCREEN_FRAMING_NO_WANT_ME_TO = (
    "A read-only discovery command was just run to answer the user's question; "
    "its output is below. Summarize it to answer the user's question directly, "
    "citing the relevant status. The output is already on screen, so keep "
    "**Here's what that looks like:** brief or omit it when it would repeat "
    "what the user just saw."
)

_OFF_SCREEN_FRAMING = (
    "Live data was just gathered from the connected integrations to answer the "
    "user's question; the tool results are below and are NOT otherwise shown to "
    "the user. Answer using the three-part response shape from the system "
    "prompt: **I found:**, **Here's what that looks like:**, and **Want me to:** "
    "with a specific next step. Cite concrete findings (issues, log lines, or "
    "metrics). If the data does not contain the answer, say so plainly. You have "
    "ALREADY queried the connected sources, so do NOT tell the user to paste an "
    "alert or to run `opensre investigate`; instead report what each source "
    "returned and, if you need more signal, ask for the specific detail (error "
    "string, service, version, or time window) that would let you narrow it down "
    "here."
)

_OFF_SCREEN_FRAMING_NO_WANT_ME_TO = (
    "Live data was just gathered from the connected integrations to answer the "
    "user's question; the tool results are below and are NOT otherwise shown to "
    "the user. Answer using **I found:** and **Here's what that looks like:** — "
    "omit **Want me to:** entirely (an session goal owns continuation). "
    "Cite concrete findings (issues, log lines, or metrics). If the data does "
    "not contain the answer, say so plainly. You have ALREADY queried the "
    "connected sources, so do NOT tell the user to paste an alert or to run "
    "`opensre investigate`; instead report what each source returned and, if "
    "you need more signal, ask for the specific detail (error string, service, "
    "version, or time window) that would let you narrow it down here."
)

_CLOSER_WANT_ME_TO = (
    "Do NOT request, plan, or emit any further tool calls or actions in this "
    "turn — phrase next steps only as prose in **Want me to:**."
)

_CLOSER_NO_WANT_ME_TO = (
    "Do NOT request, plan, or emit any further tool calls or actions in this "
    "turn. Do NOT close with **Want me to:** — phrase any next diagnostic step "
    "as plain prose in the body if the goal asks for it."
)


def _guidance_for_handoff_tag(tag: str) -> str | None:
    """Resolve exact handoff tags, then known prefixes (e.g. ``database_query:``)."""
    if tag in HANDOFF_GUIDANCE:
        return HANDOFF_GUIDANCE[tag]
    for prefix in _HANDOFF_GUIDANCE_PREFIXES:
        if tag.startswith(prefix):
            return HANDOFF_GUIDANCE.get(prefix)
    return None


def build_handoff_guidance_block(handoff_contents: tuple[str, ...]) -> str:
    """Render topic-specific assistant guidance from action-planner handoff tags."""
    blocks = [
        guidance
        for tag in handoff_contents
        if (guidance := _guidance_for_handoff_tag(tag)) is not None
    ]
    return "".join(blocks)


def build_observation_block(
    tool_observation: str | None,
    *,
    on_screen: bool = True,
    omit_want_me_to: bool = False,
) -> str:
    """Wrap freshly-gathered tool output so the assistant summarizes it directly.

    When ``omit_want_me_to`` is true (session goal active), skip the
    Want-me-to closer — the host loop owns continuation.
    """
    if not tool_observation or not tool_observation.strip():
        return ""
    if on_screen:
        framing = _ON_SCREEN_FRAMING_NO_WANT_ME_TO if omit_want_me_to else _ON_SCREEN_FRAMING
    else:
        framing = _OFF_SCREEN_FRAMING_NO_WANT_ME_TO if omit_want_me_to else _OFF_SCREEN_FRAMING
    closer = _CLOSER_NO_WANT_ME_TO if omit_want_me_to else _CLOSER_WANT_ME_TO
    return f"{framing} {closer}\n\n--- tool_results ---\n{tool_observation}\n\n"


# Legacy private name used by older tests.
_build_observation_block = build_observation_block

__all__ = [
    "HANDOFF_GUIDANCE",
    "_build_observation_block",
    "build_handoff_guidance_block",
    "build_observation_block",
]

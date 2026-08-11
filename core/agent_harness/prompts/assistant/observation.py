"""Per-turn user-half blocks: tool observation and action-planner handoffs."""

from __future__ import annotations

from core.agent_harness.prompts.assistant.text import HANDOFF_GUIDANCE

# Prefix keys in ``HANDOFF_GUIDANCE`` (trailing ``:``) match any tag with that prefix.
# Longer ``L0_degraded:config`` must precede ``L0_degraded`` so config tags win.
_HANDOFF_GUIDANCE_PREFIXES: tuple[str, ...] = (
    "evidence_tier:L0_degraded:config",
    "evidence_tier:L0_degraded",
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
    "_build_observation_block",
    "build_handoff_guidance_block",
    "build_observation_block",
]

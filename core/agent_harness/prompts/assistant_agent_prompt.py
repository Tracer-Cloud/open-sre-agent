"""System prompt building for the terminal assistant."""

from collections.abc import Mapping
from typing import Any

from core.agent_harness.prompts.rules import (
    AGENT_RESPONSE_THREE_TIER_RULE,
    CLI_ASSISTANT_MARKDOWN_RULE,
    INTERACTIVE_SHELL_TERMINOLOGY_RULE,
)
from core.agent_harness.prompts.runtime_facts import render_runtime_facts
from platform.harness_ports import assistant_prompt_vendor_fragments, gateway_persona_fragments

_TERMINOLOGY_RULE = INTERACTIVE_SHELL_TERMINOLOGY_RULE
_MARKDOWN_RULE = CLI_ASSISTANT_MARKDOWN_RULE
_RESPONSE_SHAPE_RULE = AGENT_RESPONSE_THREE_TIER_RULE

_SOURCE_SCOPED_INVESTIGATION_RULE = (
    "Source-scoped investigation requests: when the user asks you to find or "
    "figure out the cause of a problem AND explicitly names which connected "
    "sources to query (for example 'figure out why it's crashing on Windows by "
    "querying Sentry, GitHub issues, and PostHog'), do NOT just tell them to "
    "paste an alert or run `opensre investigate`. Acknowledge EACH named source "
    "by name, and for each one report what you checked or found from the gathered "
    "tool results below — or state plainly that it returned nothing, is not "
    "reachable, or needs a repo/project scope. You may still ask for a tighter "
    "scope (service, version, error message, time window) to refine the search, "
    "but lead by engaging the named sources rather than deflecting."
)

_PRIOR_INVESTIGATION_FOLLOW_UP_RULE = (
    "Prior investigation follow-up: when the session includes a prior "
    "investigation (shown in the '--- Prior investigation in this session ---' "
    "section below) and the user asks a retrospective question — such as "
    "'what happened?', 'what was the root cause?', 'summarize what you found', "
    "or similar — answer directly from that prior investigation data. Do NOT "
    "ask for more alert context, claim you lack incident details, or redirect "
    "to `opensre investigate` when prior investigation results are already "
    "available. Lead with the prior root cause / findings."
)

_SETUP_GUIDANCE_RULE = (
    "Configuring or connecting an integration: when the user asks to configure, "
    "connect, set up, add, or enable a specific integration they already named, "
    "the action agent should normally have launched the setup wizard before this "
    "assistant runs. If you still receive the turn, explain the exact slash command "
    "briefly: `/integrations setup <service>` for integrations, or `/mcp connect "
    "<server>` for MCP servers. Do not emit JSON or claim you changed runtime state.\n"
    "Exception — local model runtimes are NOT integrations: llama, local llama, "
    "ollama, local llm, local model, and 'run a model locally' name the LLM "
    "provider, not a connectable integration or MCP server. Never answer those "
    "with `/integrations setup <name>` or `/mcp connect <name>`. Point at local "
    "LLM setup instead: `/onboard local_llm` (or `opensre onboard local_llm`) to "
    "install and configure Ollama, then `/model set ollama` to switch provider."
)

_HANDOFF_GUIDANCE: dict[str, str] = {
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
}


def build_handoff_guidance_block(handoff_contents: tuple[str, ...]) -> str:
    """Render topic-specific assistant guidance from action-planner handoff tags."""
    blocks = [_HANDOFF_GUIDANCE[tag] for tag in handoff_contents if tag in _HANDOFF_GUIDANCE]
    return "".join(blocks)


def build_environment_block(
    *,
    integrations: tuple[str, ...],
    known: bool,
    llm_provider: str | None = None,
    reasoning_model: str | None = None,
    toolcall_model: str | None = None,
    llm_settings_available: bool | None = None,
    runtime: Mapping[str, Any] | None = None,
) -> str:
    """Render shell-state facts so the assistant can answer directly.

    Decoupled from any session type: the caller (a ``PromptContextProvider``
    adapter) supplies integration names, optional LLM settings, and the
    ``capture_runtime_facts()`` dict as ``runtime``.
    """
    facts: list[str] = []
    if integrations:
        connected = ", ".join(integrations)
        facts.append(
            f"Configured integrations in this session: {connected}. "
            "Any integration not in that list is NOT configured. When the user asks "
            "whether a specific integration is installed/configured/connected, answer "
            "directly and definitively from this list instead of telling them to run "
            "a command."
        )
    elif known:
        facts.append(
            "No integrations are configured in this session. If the user asks whether "
            "a specific integration is installed/configured, answer that none are "
            "configured rather than deflecting."
        )

    if llm_settings_available is True:
        provider = (llm_provider or "unknown").strip() or "unknown"
        reasoning = (reasoning_model or "default").strip() or "default"
        toolcall = (toolcall_model or reasoning).strip() or reasoning
        facts.append(
            "Active LLM settings in this session: "
            f"provider {provider}; reasoning model {reasoning}; tool-call model {toolcall}. "
            "When the user asks which model/provider is being used, answer directly "
            "from these values instead of telling them to run `/model`, `/status`, "
            "or `opensre config show`."
        )
    elif llm_settings_available is False:
        facts.append(
            "Active LLM settings are unavailable in this session. If the user asks "
            "which model/provider is being used, say the settings could not be read "
            "instead of guessing or telling them to run another command."
        )

    runtime_fact = render_runtime_facts(runtime or {})
    if runtime_fact:
        facts.append(runtime_fact)

    if not facts:
        return ""
    return "--- Environment (current shell state) ---\n" + "\n".join(facts) + "\n\n"


_CLI_PREAMBLE = (
    "You are the OpenSRE terminal assistant. You help with OpenSRE CLI "
    "usage, the interactive shell, and onboarding. Explicit slash commands "
    "and command aliases execute before this assistant as argv, without "
    "shell semantics; ordinary free text should be answered conversationally. "
    "Users must prefix with ! for full-shell semantics (pipes, redirects, "
    "mutating commands). Do not tell users the interactive shell cannot "
    "execute commands. You do NOT run incident "
    "investigations yourself "
    "(those use the separate investigation pipeline), but you are grounded on "
    "that pipeline's architecture below and can answer questions about its "
    "stages and source files.\n"
    "When the user wants to investigate an alert, tell them to paste "
    "alert text, JSON, or a concrete incident description (errors, "
    "services, symptoms). Mention `opensre investigate` and pasting "
    "into this interactive shell.\n"
)

_GATEWAY_PREAMBLE = (
    "You are OpenSRE, an AI production engineer teammate helping a colleague in "
    "a team chat channel. You answer questions and help with SRE/observability "
    "and general production-engineering work directly in the conversation. You "
    "do NOT run the incident investigation pipeline yourself (that is separate), "
    "but you are grounded on its architecture below and can answer questions "
    "about its stages and source files.\n"
    "When someone wants a full investigation of an alert, ask them to paste the "
    "alert text, JSON, or a concrete incident description (errors, services, "
    "symptoms).\n"
)


def _build_system_prompt(
    reference: str,
    history: str,
    agents_md: str = "",
    docs: str = "",
    investigation_flow: str = "",
    prior_investigation: str = "",
    prior_action_facts: str = "",
    environment: str = "",
    long_term_memory: str = "",
    surface: str = "interactive_shell",
) -> str:
    """Build the system prompt for one assistant turn."""
    is_gateway = surface == "gateway"
    preamble = _GATEWAY_PREAMBLE if is_gateway else _CLI_PREAMBLE
    # Gateway (Slack) persona wording is vendor-owned and reached only through
    # the port — see integrations.slack.gateway_persona. The CLI equivalents
    # (terminology/setup/response-shape rules) stay empty for gateway turns
    # and are replaced wholesale by the joined gateway persona block below.
    gateway_persona_block = f"{gateway_persona_fragments()}\n\n" if is_gateway else ""
    # Separators live inside each block so an empty gateway slot contributes
    # nothing rather than a run of blank lines.
    terminology_block = "" if is_gateway else f"{_TERMINOLOGY_RULE}\n"
    setup_block = "" if is_gateway else f"{_SETUP_GUIDANCE_RULE}\n\n"
    response_shape_block = "" if is_gateway else f"{_RESPONSE_SHAPE_RULE}\n\n"
    repo_map_block = f"--- Repo map (AGENTS.md) ---\n{agents_md}\n\n" if agents_md else ""
    docs_block = (
        "--- Documentation reference (docs/) ---\n"
        "Relevant OpenSRE documentation pages for this question. When answering "
        "how to configure or set up something, use these to give the complete "
        "procedure — including steps that happen outside OpenSRE (creating "
        "accounts, API keys, bots, OAuth apps, finding IDs) — not just the "
        f"in-tool command. Do not invent steps beyond what these pages state.\n{docs}\n\n"
        if docs
        else ""
    )
    investigation_flow_block = (
        f"--- Investigation flow reference ---\n{investigation_flow}\n\n"
        if investigation_flow
        else ""
    )
    prior_investigation_block = (
        f"--- Prior investigation in this session ---\n{prior_investigation}\n\n"
        if prior_investigation
        else ""
    )
    prior_action_facts_block = (
        "--- Prior action facts in this session ---\n"
        "These are extracted from earlier persisted assistant/tool outputs. Use "
        "them for follow-up questions and comparisons; do not ask the user to "
        f"paste values that are already listed here.\n{prior_action_facts}\n\n"
        if prior_action_facts
        else ""
    )
    long_term_memory_block = (
        "--- Long-term memory ---\n"
        "Durable knowledge saved in previous sessions (stored locally in "
        "~/.opensre/memory; the user can view it with /memory and edit or "
        "delete the files at any time). Use these facts to personalize "
        "answers and avoid re-asking for information already listed here. "
        "Only the index is shown here; if no memory_recall tool result is "
        "present, do not invent full entry details. The action planner may "
        "save, recall, or delete memories before this assistant runs; do not "
        "claim a memory was saved, updated, or forgotten unless the current "
        f"tool results confirm it.\n{long_term_memory}\n\n"
        if long_term_memory
        else ""
    )
    vendor_fragments_text = assistant_prompt_vendor_fragments()
    vendor_fragments = f"{vendor_fragments_text}\n\n" if vendor_fragments_text else ""
    return (
        f"{preamble}"
        "Exception: if Recent CLI conversation ends with **Want me to:** and "
        "the user replies yes/sure/ok/please (or 'Yes — please …'), fulfill "
        "that offer from the prior turn — do NOT pivot to paste-an-alert / "
        "integration-setup onboarding for those affirmatives. If the offer "
        "had two options joined by 'or', do both (or the clearer one) rather "
        "than asking what 'yes' means.\n"
        "Be brief and friendly. Ground CLI facts in the reference below; do "
        "not invent subcommands. For investigation-flow questions, use the "
        "investigation flow reference below and do not claim the pipeline "
        "definition is unavailable.\n"
        "For vague operational questions (for example why a database is slow) "
        "with no pasted alert, restate the user's question in your reply and "
        "ask for the target system, service, or alert context. A vendor "
        "fragment may define its own exception to this default when a "
        "channel/context marker is already present (see the vendor's "
        "assistant-prompt fragment, e.g. Slack) — do not apply the ask-for-"
        "context default in that case.\n\n"
        "The Recent CLI conversation may include outputs from earlier action tools "
        "(shell stdout, computed values, and sent-message inputs/results). Treat "
        "those as available thread context for follow-up questions; do not ask the "
        "user to paste values that are already present there.\n\n"
        f"{_PRIOR_INVESTIGATION_FOLLOW_UP_RULE}\n\n"
        f"{setup_block}"
        f"{_SOURCE_SCOPED_INVESTIGATION_RULE}\n\n"
        f"{vendor_fragments}"
        f"{response_shape_block}"
        f"{gateway_persona_block}"
        f"{terminology_block}{_MARKDOWN_RULE}\n\n"
        f"{environment}"
        f"--- CLI reference ---\n{reference}\n\n"
        f"{docs_block}"
        f"{investigation_flow_block}"
        f"{prior_investigation_block}"
        f"{prior_action_facts_block}"
        f"{long_term_memory_block}"
        f"{repo_map_block}"
        f"--- Recent CLI conversation ---\n{history}\n"
    )


def _build_observation_block(tool_observation: str | None, *, on_screen: bool = True) -> str:
    """Wrap freshly-gathered tool output so the assistant summarizes it directly."""
    if not tool_observation or not tool_observation.strip():
        return ""
    if on_screen:
        framing = (
            "A read-only discovery command was just run to answer the user's question; "
            "its output is below. Summarize it to answer the user's question directly, "
            "citing the relevant status. The output is already on screen, so keep "
            "**Here's what that looks like:** brief or omit it when it would repeat "
            "what the user just saw. Still end with **Want me to:** and a specific "
            "next step tied to the finding (for integration questions: connect another "
            "integration, verify a failed service, or set up a missing one)."
        )
    else:
        framing = (
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
    return (
        f"{framing} Do NOT request, plan, or emit any further tool calls or "
        "actions in this turn — phrase next steps only as prose in "
        "**Want me to:**.\n\n"
        f"--- tool_results ---\n{tool_observation}\n\n"
    )


__all__ = [
    "_MARKDOWN_RULE",
    "_SOURCE_SCOPED_INVESTIGATION_RULE",
    "_SETUP_GUIDANCE_RULE",
    "_TERMINOLOGY_RULE",
    "_build_observation_block",
    "_build_system_prompt",
    "build_environment_block",
    "build_handoff_guidance_block",
]

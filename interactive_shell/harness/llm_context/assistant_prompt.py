"""Conversational assistant prompt construction for the interactive OpenSRE shell.

Builds the full docs-aware assistant prompt from grounding sources, prior
investigation state, environment facts, synthetic-run observations, and recent
conversation history. The turn runtime (``harness/agent.py``) calls
``build_cli_agent_prompt`` and stays out of the business of assembling prompt
text.

Prompt blocks are composed from typed :class:`PromptSection` values via
``render_sections`` so the exact ``--- label ---`` spacing lives in one place;
free-form framing blocks (observation, integration guard, synthetic failure)
stay as small pure ``-> str`` builders because their bodies are bespoke prose.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from interactive_shell.harness.agent_context import AgentContext
from interactive_shell.harness.llm_context.conversation_history import format_recent_conversation
from interactive_shell.harness.llm_context.grounding.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)
from interactive_shell.harness.llm_context.models import PromptSection, render_sections
from interactive_shell.harness.llm_context.rules import (
    ACTION_RULE,
    CLI_ASSISTANT_MARKDOWN_RULE,
    INTERACTIVE_SHELL_TERMINOLOGY_RULE,
    SETUP_GUIDANCE_RULE,
    SOURCE_SCOPED_INVESTIGATION_RULE,
)
from interactive_shell.harness.llm_context.session import (
    SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
)
from interactive_shell.runtime import ReplSession

_logger = logging.getLogger(__name__)

_MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS = 120_000

_ASSISTANT_INTRO = (
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
    "Be brief and friendly. Ground CLI facts in the reference below; do "
    "not invent subcommands. For investigation-flow questions, use the "
    "investigation flow reference below and do not claim the pipeline "
    "definition is unavailable.\n"
    "For vague operational questions (for example why a database is slow) "
    "with no pasted alert, restate the user's question in your reply and "
    "ask for the target system, service, or alert context.\n\n"
)


def build_environment_block(session: ReplSession) -> str:
    """Render configured-integration facts so the assistant can answer directly."""
    if not session.configured_integrations_known:
        return ""
    if session.configured_integrations:
        connected = ", ".join(session.configured_integrations)
        body = (
            f"Configured integrations in this session: {connected}. "
            "Any integration not in that list is NOT configured. When the user asks "
            "whether a specific integration is installed/configured/connected, answer "
            "directly and definitively from this list instead of telling them to run "
            "a command."
        )
    else:
        body = (
            "No integrations are configured in this session. If the user asks whether "
            "a specific integration is installed/configured, answer that none are "
            "configured rather than deflecting."
        )
    return PromptSection(label="Environment (configured integrations)", body=body).render()


def build_assistant_system_prompt(
    reference: str,
    history: str,
    agents_md: str = "",
    investigation_flow: str = "",
    prior_investigation: str = "",
    environment: str = "",
) -> str:
    """Build the system prompt for one assistant turn."""
    grounding_sections = render_sections(
        [
            PromptSection(label="Investigation flow reference", body=investigation_flow),
            PromptSection(label="Prior investigation in this session", body=prior_investigation),
            PromptSection(label="Repo map (AGENTS.md)", body=agents_md),
        ]
    )
    return (
        f"{_ASSISTANT_INTRO}"
        f"{SETUP_GUIDANCE_RULE}\n\n"
        f"{SOURCE_SCOPED_INVESTIGATION_RULE}\n\n"
        f"{INTERACTIVE_SHELL_TERMINOLOGY_RULE}\n{CLI_ASSISTANT_MARKDOWN_RULE}\n{ACTION_RULE}\n\n"
        f"{environment}"
        f"--- CLI reference ---\n{reference}\n\n"
        f"{grounding_sections}"
        f"--- Recent CLI conversation ---\n{history}\n"
    )


def build_observation_block(tool_observation: str | None, *, on_screen: bool = True) -> str:
    """Wrap freshly-gathered tool output so the assistant summarizes it directly."""
    if not tool_observation or not tool_observation.strip():
        return ""
    if on_screen:
        framing = (
            "A read-only discovery command was just run to answer the user's question; "
            "its output is below. Summarize it to answer the user's question directly "
            "and concisely (for example, whether a specific integration is configured), "
            "citing the relevant status. The output is already on screen, so keep it "
            "short."
        )
    else:
        framing = (
            "Live data was just gathered from the connected integrations to answer the "
            "user's question; the tool results are below and are NOT otherwise shown to "
            "the user. Answer the user's question directly using these results, citing "
            "the concrete findings (e.g. relevant issues, log lines, or metrics). If the "
            "data does not contain the answer, say so plainly. You have ALREADY queried "
            "the connected sources, so do NOT tell the user to paste an alert or to run "
            "`opensre investigate`; instead report what each source returned and, if you "
            "need more signal, ask for the specific detail (error string, service, "
            "version, or time window) that would let you narrow it down here."
        )
    return (
        f"{framing} Do NOT request, plan, or emit any further actions — just answer in "
        "plain Markdown.\n\n"
        f"--- tool_results ---\n{tool_observation}\n\n"
    )


def _summarize_evidence(evidence: Any) -> list[str]:
    """Render a short evidence preview for the prior-investigation grounding block.

    ``AgentState.evidence`` is a ``dict[str, Any]`` keyed by evidence id, but
    we accept list/other shapes defensively so an unexpected value doesn't
    silently drop all grounding context.
    """
    if isinstance(evidence, dict):
        sample_keys = list(evidence)[:3]
        sample = {key: evidence[key] for key in sample_keys}
        return [
            f"Evidence items: {len(evidence)}",
            "Evidence keys: " + ", ".join(map(str, sample_keys)),
            "Sample evidence:\n" + json.dumps(sample, indent=2, default=str)[:1500],
        ]
    if isinstance(evidence, list):
        return [
            f"Evidence items: {len(evidence)}",
            "Sample evidence:\n" + json.dumps(evidence[:3], indent=2, default=str)[:1500],
        ]
    return [
        f"Evidence type: {type(evidence).__name__}",
        f"Evidence summary:\n{str(evidence)[:1500]}",
    ]


def _summarize_last_state(state: dict[str, Any]) -> str:
    """Produce a compact text summary of the previous investigation for grounding."""
    parts: list[str] = []
    alert_name = state.get("alert_name")
    if alert_name:
        parts.append(f"Alert: {alert_name}")
    root_cause = state.get("root_cause")
    if root_cause:
        parts.append(f"Root cause: {root_cause}")
    problem_md = state.get("problem_md") or ""
    if problem_md:
        parts.append(f"Problem summary:\n{problem_md[:2000]}")
    slack_message = state.get("slack_message") or ""
    if slack_message:
        parts.append(f"Report:\n{slack_message[:2000]}")
    evidence = state.get("evidence")
    if evidence:
        try:
            parts.extend(_summarize_evidence(evidence))
        except (TypeError, ValueError) as exc:
            # Serialization can fail on exotic evidence values; tell the LLM
            # the context was withheld rather than silently dropping it.
            _logger.warning("could not serialize evidence for grounding: %s", exc)
            parts.append("(evidence present but could not be serialized for grounding)")
    return "\n\n".join(parts) or "(no prior investigation details available)"


def _user_message_requests_synthetic_failure_explanation(message: str) -> bool:
    """True when the user is likely asking about a failed synthetic benchmark."""
    m = message.strip().lower()
    if not m:
        return False
    suggested = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST.lower().rstrip("?")
    if m.rstrip("?") == suggested:
        return True
    if "why" in m and "fail" in m:
        return True
    return "what went wrong" in m


def _load_synthetic_observation_text(
    path_str: str, *, max_chars: int = _MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS
) -> str:
    try:
        raw = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(raw) > max_chars:
        return (
            raw[:max_chars]
            + f"\n… [truncated for prompt size; observation is {len(raw)} characters total]"
        )
    return raw


def _build_integration_guard(ctx: AgentContext) -> str:
    """Render the no-integrations guidance block (pure over the snapshot)."""
    if not (ctx.configured_integrations_known and not ctx.configured_integrations):
        return ""

    return (
        "No integrations are configured in this session. You may still help the user "
        "configure one: when they ask to set up, connect, or add an integration, emit a "
        "run_interactive action for `/integrations setup <service>` (or `/mcp connect "
        "<server>`). Do NOT emit run_cli_command or slash actions to show/verify/remove "
        "integrations that are not configured; for those, answer with guidance only.\n\n"
    )


def _build_synthetic_failure_block(ctx: AgentContext) -> str:
    obs_path = ctx.last_synthetic_observation_path
    if not obs_path:
        return ""

    if not _user_message_requests_synthetic_failure_explanation(ctx.text):
        return ""

    obs_text = _load_synthetic_observation_text(obs_path)
    if not obs_text:
        return ""

    return (
        "The user is asking about a failed `opensre tests synthetic` run "
        "in this checkout. The JSON below is the saved observation "
        f"(scores, gates, stderr summary). Path: {obs_path}\n"
        "Use it to explain validation failures. Do not say nothing ran or "
        "that you lack context — the run completed and this file was written.\n\n"
        f"--- observation_json ---\n{obs_text}\n\n"
    )


def build_cli_agent_prompt(
    *,
    message: str,
    session: ReplSession,
    tool_observation: str | None,
    tool_observation_on_screen: bool,
    agent_ctx: AgentContext,
) -> str:
    """Read grounding sources / files / snapshot once and render the prompt string.

    All session and file reads happen here; the result is a single immutable
    prompt string ready to send to the reasoning LLM.
    """
    session.grounding.log_cache_diagnostics("cli_agent_grounding")

    system = build_assistant_system_prompt(
        session.grounding.cli.build_text(),
        format_recent_conversation(list(agent_ctx.conversation_messages)),
        agents_md=session.grounding.agents_md.build_text(),
        investigation_flow=build_investigation_flow_reference_text(),
        prior_investigation=(
            _summarize_last_state(agent_ctx.last_state) if agent_ctx.last_state is not None else ""
        ),
        environment=build_environment_block(session),
    )

    integration_guard = _build_integration_guard(agent_ctx)
    observation_block = build_observation_block(
        tool_observation, on_screen=tool_observation_on_screen
    )
    synthetic_block = _build_synthetic_failure_block(agent_ctx)

    return (
        f"{system}\n"
        f"{integration_guard}"
        f"{observation_block}"
        f"{synthetic_block}"
        f"--- User message ---\n{message}"
    )


__all__ = [
    "build_assistant_system_prompt",
    "build_cli_agent_prompt",
    "build_environment_block",
    "build_observation_block",
]

"""Terminal assistant prompt assembly for the interactive shell."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from core.agent_harness.prompts.assistant_agent_prompt import (
    _build_observation_block,
    _build_system_prompt,
)
from core.agent_harness.prompts.conversation_memory import (
    format_prior_action_facts,
    format_recent_conversation,
)
from core.agent_harness.session import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

if TYPE_CHECKING:
    from core.agent_harness.models.turn_snapshot import TurnSnapshot

_logger = logging.getLogger(__name__)

_MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS = 120_000


class AssistantPromptContextProvider(Protocol):
    """Grounding provider used by the surface-agnostic assistant turn."""

    def cli_reference(self) -> str:
        raise NotImplementedError

    def agents_md(self) -> str:
        raise NotImplementedError

    def investigation_flow(self) -> str:
        raise NotImplementedError

    def environment_block(self) -> str:
        raise NotImplementedError

    def suggested_synthetic_prompt(self) -> str:
        raise NotImplementedError

    def log_diagnostics(self, reason: str) -> None:
        raise NotImplementedError


def build_assistant_system_prompt(
    reference: str,
    history: str,
    agents_md: str = "",
    investigation_flow: str = "",
    prior_investigation: str = "",
    prior_action_facts: str = "",
    environment: str = "",
) -> str:
    """Build the system prompt for one assistant turn."""
    return _build_system_prompt(
        reference,
        history,
        agents_md=agents_md,
        investigation_flow=investigation_flow,
        prior_investigation=prior_investigation,
        prior_action_facts=prior_action_facts,
        environment=environment,
    )


def build_observation_block(tool_observation: str | None, *, on_screen: bool = True) -> str:
    """Wrap freshly gathered tool output for the assistant."""
    return _build_observation_block(tool_observation, on_screen=on_screen)


def _summarize_evidence(evidence: Any) -> list[str]:
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
    """Produce a compact text summary of the previous investigation."""
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
            _logger.warning("could not serialize evidence for grounding: %s", exc)
            parts.append("(evidence present but could not be serialized for grounding)")
    return "\n\n".join(parts) or "(no prior investigation details available)"


def _user_message_requests_synthetic_failure_explanation(
    message: str,
    suggested_prompt: str = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
) -> bool:
    """True when the user is likely asking about a failed synthetic benchmark."""
    m = message.strip().lower()
    if not m:
        return False
    suggested = suggested_prompt.lower().rstrip("?")
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


def _build_integration_guard(ctx: TurnSnapshot) -> str:
    """Render the no-integrations guidance block from the turn snapshot."""
    if not (ctx.configured_integrations_known and not ctx.configured_integrations):
        return ""

    return (
        "No integrations are configured in this session. You may still help the user "
        "configure one: explain `/integrations setup <service>` for integrations or "
        "`/mcp connect <server>` for MCP servers. Do not claim any integration is "
        "already connected, and for show/verify/remove requests against unconfigured "
        "integrations, answer with guidance only.\n\n"
    )


def _build_synthetic_failure_block(
    ctx: TurnSnapshot,
    *,
    suggested_prompt: str = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
) -> str:
    obs_path = ctx.last_synthetic_observation_path
    if not obs_path:
        return ""

    if not _user_message_requests_synthetic_failure_explanation(
        ctx.text,
        suggested_prompt=suggested_prompt,
    ):
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


def build_cli_agent_prompt_from_provider(
    *,
    message: str,
    prompts: AssistantPromptContextProvider,
    tool_observation: str | None,
    tool_observation_on_screen: bool,
    turn_snapshot: TurnSnapshot,
) -> str:
    """Render an assistant prompt from the core prompt-provider port."""
    prompts.log_diagnostics("cli_agent_grounding")
    system = build_assistant_system_prompt(
        prompts.cli_reference(),
        format_recent_conversation(list(turn_snapshot.conversation_messages)),
        agents_md=prompts.agents_md(),
        investigation_flow=prompts.investigation_flow(),
        prior_investigation=(
            _summarize_last_state(turn_snapshot.last_state)
            if turn_snapshot.last_state is not None
            else ""
        ),
        prior_action_facts=format_prior_action_facts(list(turn_snapshot.conversation_messages)),
        environment=prompts.environment_block(),
    )
    return (
        f"{system}\n"
        f"{_build_integration_guard(turn_snapshot)}"
        f"{build_observation_block(tool_observation, on_screen=tool_observation_on_screen)}"
        f"{_build_synthetic_failure_block(turn_snapshot, suggested_prompt=prompts.suggested_synthetic_prompt())}"
        f"--- User message ---\n{message}"
    )


__all__ = [
    "AssistantPromptContextProvider",
    "build_assistant_system_prompt",
    "build_cli_agent_prompt_from_provider",
    "build_observation_block",
]

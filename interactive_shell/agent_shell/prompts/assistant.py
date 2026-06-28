"""Terminal assistant prompt assembly for the interactive shell."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

from core.agent.conversation_memory import format_recent_conversation
from core.agent.prompts import (
    PromptEnvelope,
    _build_observation_block,
    _build_system_prompt,
    build_environment_block,
)
from core.agent.turn_context import TurnContext
from interactive_shell.agent_shell.grounding.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)
from interactive_shell.session import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

_logger = logging.getLogger(__name__)

_MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS = 120_000


class _Reference(Protocol):
    def build_text(self, *_args: Any, **_kwargs: Any) -> str: ...


class _GroundingBundle(Protocol):
    cli: _Reference
    agents_md: _Reference

    def log_cache_diagnostics(self, reason: str) -> None: ...


class ShellPromptSession(Protocol):
    """Session fields needed to render the terminal assistant prompt."""

    configured_integrations: tuple[str, ...]
    configured_integrations_known: bool
    grounding: _GroundingBundle


def build_assistant_system_prompt(
    reference: str,
    history: str,
    agents_md: str = "",
    investigation_flow: str = "",
    prior_investigation: str = "",
    environment: str = "",
) -> str:
    """Build the system prompt for one assistant turn."""
    return _build_system_prompt(
        reference,
        history,
        agents_md=agents_md,
        investigation_flow=investigation_flow,
        prior_investigation=prior_investigation,
        environment=environment,
    )


def build_observation_block(tool_observation: str | None, *, on_screen: bool = True) -> str:
    """Wrap freshly gathered tool output for the assistant."""
    return _build_observation_block(tool_observation, on_screen=on_screen)


def build_shell_environment_block(session: ShellPromptSession) -> str:
    """Render configured-integration facts from a shell session snapshot."""
    return build_environment_block(
        integrations=tuple(session.configured_integrations),
        known=bool(session.configured_integrations_known),
    )


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


def _build_integration_guard(ctx: TurnContext) -> str:
    """Render the no-integrations guidance block from the turn snapshot."""
    if not (ctx.configured_integrations_known and not ctx.configured_integrations):
        return ""

    return (
        "No integrations are configured in this session. You may still help the user "
        "configure one: when they ask to set up, connect, or add an integration, emit a "
        "run_interactive action for `/integrations setup <service>` (or `/mcp connect "
        "<server>`). Do NOT emit run_cli_command or slash actions to show/verify/remove "
        "integrations that are not configured; for those, answer with guidance only.\n\n"
    )


def _build_synthetic_failure_block(ctx: TurnContext) -> str:
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


def build_cli_agent_prompt_envelope(
    *,
    message: str,
    session: ShellPromptSession,
    tool_observation: str | None,
    tool_observation_on_screen: bool,
    turn_ctx: TurnContext,
) -> PromptEnvelope:
    """Read shell grounding sources once and return a render-compatible envelope."""
    session.grounding.log_cache_diagnostics("cli_agent_grounding")

    system = build_assistant_system_prompt(
        session.grounding.cli.build_text(),
        format_recent_conversation(list(turn_ctx.conversation_messages)),
        agents_md=session.grounding.agents_md.build_text(),
        investigation_flow=build_investigation_flow_reference_text(),
        prior_investigation=(
            _summarize_last_state(turn_ctx.last_state) if turn_ctx.last_state is not None else ""
        ),
        environment=build_shell_environment_block(session),
    )

    prompt = (
        f"{system}\n"
        f"{_build_integration_guard(turn_ctx)}"
        f"{build_observation_block(tool_observation, on_screen=tool_observation_on_screen)}"
        f"{_build_synthetic_failure_block(turn_ctx)}"
        f"--- User message ---\n{message}"
    )
    return PromptEnvelope.from_text(
        prompt,
        block_id="cli-agent-prompt",
        kind="system",
        metadata={"prompt": "cli_agent"},
    )


def build_cli_agent_prompt(
    *,
    message: str,
    session: ShellPromptSession,
    tool_observation: str | None,
    tool_observation_on_screen: bool,
    turn_ctx: TurnContext,
) -> str:
    """Read shell grounding sources once and render the assistant prompt string."""
    return build_cli_agent_prompt_envelope(
        message=message,
        session=session,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        turn_ctx=turn_ctx,
    ).render()


__all__ = [
    "ShellPromptSession",
    "build_cli_agent_prompt_envelope",
    "build_assistant_system_prompt",
    "build_cli_agent_prompt",
    "build_observation_block",
    "build_shell_environment_block",
]

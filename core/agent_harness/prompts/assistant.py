"""Terminal assistant prompt assembly for the interactive shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import core.agent_harness.prompts.synthetic_failure as synthetic_failure
from config.constants.prompts import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST
from core.agent_harness.prompts.assistant_agent_prompt import (
    _build_observation_block,
    _build_system_prompt,
    build_handoff_guidance_block,
)
from core.agent_harness.prompts.conversation_memory import (
    format_prior_action_facts,
    format_recent_conversation,
)
from core.agent_harness.prompts.prior_investigation import (
    build_block as build_prior_investigation_block,
)
from core.agent_harness.prompts.prior_investigation import (
    is_prior_investigation_follow_up,
)

if TYPE_CHECKING:
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot


class AssistantPromptContextProvider(Protocol):
    """Grounding provider used by the surface-agnostic assistant turn."""

    def surface(self) -> str:
        """Which surface this turn runs on; defaults to the interactive shell."""
        return "interactive_shell"

    def cli_reference(self) -> str:
        raise NotImplementedError

    def agents_md(self) -> str:
        raise NotImplementedError

    def docs(self, query: str) -> str:
        raise NotImplementedError

    def investigation_flow(self) -> str:
        raise NotImplementedError

    def environment_block(self) -> str:
        raise NotImplementedError

    def long_term_memory(self) -> str:
        raise NotImplementedError

    def suggested_synthetic_prompt(self) -> str:
        raise NotImplementedError

    def log_diagnostics(self, reason: str) -> None:
        raise NotImplementedError


def build_assistant_system_prompt(
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
    return _build_system_prompt(
        reference,
        history,
        agents_md=agents_md,
        docs=docs,
        investigation_flow=investigation_flow,
        prior_investigation=prior_investigation,
        prior_action_facts=prior_action_facts,
        environment=environment,
        long_term_memory=long_term_memory,
        surface=surface,
    )


def build_observation_block(tool_observation: str | None, *, on_screen: bool = True) -> str:
    """Wrap freshly gathered tool output for the assistant."""
    return _build_observation_block(tool_observation, on_screen=on_screen)


def _assistant_context_blocks(
    *,
    turn_snapshot: TurnSnapshot,
    handoff_contents: tuple[str, ...],
    tool_observation: str | None,
    tool_observation_on_screen: bool,
    suggested_prompt: str = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
) -> str:
    return (
        f"{_build_integration_guard(turn_snapshot)}"
        f"{build_handoff_guidance_block(handoff_contents)}"
        f"{build_observation_block(tool_observation, on_screen=tool_observation_on_screen)}"
        f"{synthetic_failure.build_block(turn_snapshot, suggested_prompt=suggested_prompt)}"
    )


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


def build_cli_agent_prompt_from_provider(
    *,
    message: str,
    prompts: AssistantPromptContextProvider,
    tool_observation: str | None,
    tool_observation_on_screen: bool,
    handoff_contents: tuple[str, ...] = (),
    turn_snapshot: TurnSnapshot,
) -> str:
    """Render an assistant prompt from the core prompt-provider port."""
    prompts.log_diagnostics("cli_agent_grounding")
    system = build_assistant_system_prompt(
        prompts.cli_reference(),
        format_recent_conversation(list(turn_snapshot.conversation_messages)),
        agents_md=prompts.agents_md(),
        docs=prompts.docs(message),
        investigation_flow=prompts.investigation_flow(),
        # The session's completed investigation stays available for the whole
        # session — a retrospective question can come at any point, and dropping
        # it would make OpenSRE claim it lacks incident details it still holds.
        # Age only downgrades it to background (see the note), because past the
        # recall window the turn also gathers fresh evidence.
        prior_investigation=build_prior_investigation_block(
            turn_snapshot.last_state,
            explicit_follow_up=is_prior_investigation_follow_up(handoff_contents),
        ),
        prior_action_facts=format_prior_action_facts(list(turn_snapshot.conversation_messages)),
        environment=prompts.environment_block(),
        long_term_memory=prompts.long_term_memory(),
        surface=prompts.surface(),
    )
    return (
        f"{system}\n"
        f"{_assistant_context_blocks(turn_snapshot=turn_snapshot, handoff_contents=handoff_contents, tool_observation=tool_observation, tool_observation_on_screen=tool_observation_on_screen, suggested_prompt=prompts.suggested_synthetic_prompt())}"
        f"--- User message ---\n{message}"
    )


__all__ = [
    "AssistantPromptContextProvider",
    "build_assistant_system_prompt",
    "build_cli_agent_prompt_from_provider",
    "build_observation_block",
]

"""LLM/tool-calling boundary for action planner."""

from __future__ import annotations

import logging
from typing import Any

from app.cli.interactive_shell.token_accounting import record_invoke_response

from .constants import _OPENAI_STYLE_PROVIDERS, _USER_TEMPLATE
from .prompting import _connected_integrations_block, _recent_conversation_block, _system_prompt

logger = logging.getLogger(__name__)


def _tool_specs_for_provider(session: Any) -> list[dict[str, Any]]:
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
        REGISTRY,
    )
    from app.cli.interactive_shell.runtime.session import ReplSession
    from app.config import resolve_llm_settings

    provider = resolve_llm_settings().provider
    base_specs = REGISTRY.tool_specs_for_llm(session or ReplSession())
    if provider in _OPENAI_STYLE_PROVIDERS:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["input_schema"],
                },
            }
            for spec in base_specs
        ]
    return base_specs


def _call_llm(sanitised_text: str, session: Any) -> str | None:
    try:
        from app.services.llm_client import get_llm_for_classification
    except Exception as exc:
        logger.warning(
            "llm_action_planner: LLM client import failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return None

    prompt = (
        _system_prompt()
        + "\n\n"
        + _connected_integrations_block(session)
        + _recent_conversation_block(session)
        + _USER_TEMPLATE.format(text=sanitised_text)
    )
    try:
        client = get_llm_for_classification().bind_tools(_tool_specs_for_provider(session))
        response = client.invoke(prompt)
        return record_invoke_response(session, prompt=prompt, response=response)
    except Exception as exc:
        logger.debug(
            "llm_action_planner: LLM call failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        # Raise a typed error so the caller can surface the reason in the
        # assistant block instead of printing a raw log warning above it.
        from app.cli.interactive_shell.routing.handle_message_with_agent.errors import (
            PlannerLLMError,
        )
        from app.config import llm_provider_error_context

        # Prefix the raw provider error with which provider actually served the
        # request (and whether it was a fallback). This turns a confusing
        # "Anthropic credit balance too low" into an actionable message when the
        # user configured OpenAI but its key was missing.
        context = llm_provider_error_context()
        message = f"{context} {exc}".strip() if context else str(exc)
        raise PlannerLLMError(message) from exc

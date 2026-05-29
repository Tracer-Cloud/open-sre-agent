"""LLM/tool-calling boundary for action planner."""

from __future__ import annotations

import logging
from typing import Any

from .constants import _OPENAI_STYLE_PROVIDERS, _USER_TEMPLATE
from .prompting import _system_prompt

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

    prompt = _system_prompt() + "\n\n" + _USER_TEMPLATE.format(text=sanitised_text)
    try:
        client = get_llm_for_classification().bind_tools(_tool_specs_for_provider(session))
        response = client.invoke(prompt)
        return response.content.strip()
    except Exception as exc:
        logger.warning(
            "llm_action_planner: LLM call failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return None

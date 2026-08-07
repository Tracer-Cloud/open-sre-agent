"""Session environment facts for the assistant CONTEXT tier."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness.prompts.runtime_facts import render_runtime_facts


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

    # Live keys (now_iso / uptime / disk / memory) stay out of this block so
    # the system/env prefix remains cache-stable across turns.
    runtime_fact = render_runtime_facts(runtime or {})
    if runtime_fact:
        facts.append(runtime_fact)

    if not facts:
        return ""
    return "".join(("--- Environment (current shell state) ---\n", "\n".join(facts), "\n\n"))


__all__ = ["build_environment_block"]

from __future__ import annotations

import importlib.util

import core.context as context


def test_context_package_has_no_shell_prompt_exports() -> None:
    """Core context should not expose shell prompt/runtime request helpers."""
    forbidden_exports = {
        "AgentContext",
        "SYSTEM_PROMPT_BASE",
        "build_action_system_prompt",
        "build_action_user_message",
        "connected_integrations_block",
        "recent_conversation_block",
        "sanitize_action_text",
    }

    assert context.__all__ == []
    assert forbidden_exports.isdisjoint(vars(context))


def test_top_level_context_package_is_removed() -> None:
    """The canonical import path is core.context, with no compatibility shim."""
    assert importlib.util.find_spec("context") is None

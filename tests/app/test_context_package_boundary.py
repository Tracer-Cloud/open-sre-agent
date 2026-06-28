from __future__ import annotations

import context


def test_context_package_has_no_shell_prompt_exports() -> None:
    """Top-level context should not expose shell prompt/runtime request helpers."""
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

from __future__ import annotations

import importlib.util

import core.state as state


def test_state_package_has_no_shell_prompt_exports() -> None:
    """Core state should not expose shell prompt/runtime request helpers."""
    forbidden_exports = {
        "AgentContext",
        "SYSTEM_PROMPT_BASE",
        "build_action_system_prompt",
        "build_action_user_message",
        "connected_integrations_block",
        "recent_conversation_block",
        "sanitize_action_text",
    }

    assert forbidden_exports.isdisjoint(vars(state))


def test_old_context_package_is_removed() -> None:
    """``core/context/`` was collapsed into ``core/state/``; no compatibility shim remains."""
    assert importlib.util.find_spec("core.context") is None
    assert importlib.util.find_spec("context") is None

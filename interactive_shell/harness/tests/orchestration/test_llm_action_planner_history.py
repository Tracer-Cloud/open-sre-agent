"""Planner uses the shared recent-conversation context to resolve follow-ups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from interactive_shell.harness.orchestration.llm_action_planner import (
    llm_client,
)
from interactive_shell.harness.orchestration.llm_action_planner.prompting import (
    _connected_integrations_block,
    _recent_conversation_block,
)
from interactive_shell.harness.orchestration.llm_action_planner.system_prompt import (
    _SYSTEM_PROMPT_BASE,
)
from interactive_shell.harness.state.conversation_history import NO_HISTORY_PLACEHOLDER


@dataclass
class _FakeSession:
    cli_agent_messages: list[tuple[str, str]] = field(default_factory=list)
    configured_integrations: tuple[str, ...] = ()
    configured_integrations_known: bool = False


def test_block_contains_history_lines() -> None:
    session = _FakeSession(
        cli_agent_messages=[
            ("user", "how can I remove github integration"),
            ("assistant", "Use /integrations remove github or /integrations list."),
        ]
    )
    block = _recent_conversation_block(session)
    assert "RECENT CONVERSATION" in block
    assert "User: how can I remove github integration" in block
    assert "Assistant: Use /integrations remove github or /integrations list." in block


def test_block_placeholder_without_history() -> None:
    block = _recent_conversation_block(_FakeSession())
    assert NO_HISTORY_PLACEHOLDER in block


def test_system_prompt_documents_followup_resolution() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "do both" in prompt
    assert "recent conversation" in prompt
    # The planner must hand off when it cannot resolve the referent.
    assert "assistant_handoff" in prompt


def test_call_llm_prompt_includes_recent_conversation(monkeypatch: Any) -> None:
    """The prompt sent to the planner LLM must carry the shared conversation block."""
    captured: dict[str, str] = {}

    class _FakeClient:
        def bind_tools(self, _specs: object) -> _FakeClient:
            return self

        def invoke(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return "ok"

    monkeypatch.setattr(llm_client, "_tool_specs_for_provider", lambda _session: [])
    monkeypatch.setattr(llm_client, "record_invoke_response", lambda *_a, **_k: "ok")
    monkeypatch.setattr(
        "core.runtime.llm.llm_client.get_llm_for_classification",
        lambda: _FakeClient(),
    )

    session = _FakeSession(
        cli_agent_messages=[
            ("user", "how can I remove github integration"),
            ("assistant", "Use /integrations remove github or /integrations list."),
        ]
    )
    llm_client._call_llm("do both for me", session)

    prompt = captured["prompt"]
    assert "RECENT CONVERSATION" in prompt
    assert "User: how can I remove github integration" in prompt
    assert "USER MESSAGE (literal): <<<do both for me>>>" in prompt


def test_connected_integrations_block_renders_state() -> None:
    """The block surfaces the integration set the planner gates dispatch on."""
    # Unknown: no session / not yet resolved → planner must NOT dispatch implicit
    # diagnostic questions.
    assert "unknown" in _connected_integrations_block(None)
    assert "unknown" in _connected_integrations_block(_FakeSession())
    # Known-but-empty → "none" (no-integration handoff path, e.g. scenario 313).
    none_block = _connected_integrations_block(
        _FakeSession(configured_integrations=(), configured_integrations_known=True)
    )
    assert "none" in none_block
    assert "explicit investigate instructions still emit investigation_start" in none_block.lower()
    # Known with integrations → sorted listing the planner can gate on.
    listed = _connected_integrations_block(
        _FakeSession(
            configured_integrations=("sentry", "github", "posthog_mcp"),
            configured_integrations_known=True,
        )
    )
    assert "github, posthog_mcp, sentry" in listed


def test_call_llm_prompt_includes_connected_integrations(monkeypatch: Any) -> None:
    """The planner prompt must carry the CONNECTED INTEGRATIONS gate line."""
    captured: dict[str, str] = {}

    class _FakeClient:
        def bind_tools(self, _specs: object) -> _FakeClient:
            return self

        def invoke(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return "ok"

    monkeypatch.setattr(llm_client, "_tool_specs_for_provider", lambda _session: [])
    monkeypatch.setattr(llm_client, "record_invoke_response", lambda *_a, **_k: "ok")
    monkeypatch.setattr(
        "core.runtime.llm.llm_client.get_llm_for_classification",
        lambda: _FakeClient(),
    )

    session = _FakeSession(
        configured_integrations=("github", "sentry"),
        configured_integrations_known=True,
    )
    llm_client._call_llm("figure out why the agent crashes on windows", session)

    assert "CONNECTED INTEGRATIONS (this install, right now): github, sentry" in captured["prompt"]

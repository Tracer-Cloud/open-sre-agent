"""Core applies the surface-injected hidden-integration set (transport-agnostic)."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.prompts.prompt_context import DefaultPromptContextProvider


def _session(integrations: list[str], cache: dict) -> SimpleNamespace:
    return SimpleNamespace(
        configured_integrations=tuple(integrations),
        resolved_integrations_cache=cache,
        configured_integrations_known=True,
    )


def test_hidden_integrations_are_filtered_out() -> None:
    # Arrange: the gateway injected telegram as hidden (a Slack turn).
    session = _session(
        ["slack", "telegram", "github"], {"_gateway_hidden_integrations": ("telegram",)}
    )
    provider = DefaultPromptContextProvider(session, surface="gateway")

    # Act / Assert: only the injected names are dropped; core knows no transports.
    assert provider._visible_integrations() == ("slack", "github")


def test_no_hidden_set_shows_everything() -> None:
    session = _session(["slack", "telegram", "github"], {})
    provider = DefaultPromptContextProvider(session, surface="interactive_shell")
    assert provider._visible_integrations() == ("slack", "telegram", "github")

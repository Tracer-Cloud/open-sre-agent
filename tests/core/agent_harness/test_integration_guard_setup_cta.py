"""Assistant integration guard — no invented setup vendors (S5)."""

from __future__ import annotations

import platform.harness_ports as harness_ports
from core.agent_harness.prompts.assistant.turn import _build_integration_guard
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _snapshot(*, connected: tuple[str, ...], known: bool = True) -> TurnSnapshot:
    return TurnSnapshot(
        text="which integrations?",
        conversation_messages=(),
        configured_integrations=connected,
        configured_integrations_known=known,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        setup_state="",
    )


def test_integration_guard_lists_setupable_ids_and_blocks_invented_analytics() -> None:
    harness_ports.set_setupable_integration_services(
        lambda: ("posthog_mcp", "grafana", "sentry_mcp")
    )
    harness_ports.clear_preferred_evidence_sources()
    harness_ports.register_preferred_evidence_source("metric_read", "posthog_mcp")

    text = _build_integration_guard(_snapshot(connected=("posthog_mcp", "grafana", "github")))

    assert "posthog_mcp" in text
    assert "Only these service ids are valid" in text
    assert "mixpanel" in text.lower()  # named as forbidden example
    assert "already covered" in text.lower()
    assert "product analytics" in text.lower()


def test_integration_guard_empty_when_integrations_unknown() -> None:
    assert _build_integration_guard(_snapshot(connected=(), known=False)) == ""

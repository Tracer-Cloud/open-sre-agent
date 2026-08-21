"""Assistant integration guard — no invented setup vendors."""

from __future__ import annotations

import infrastructure.harness_ports as harness_ports
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


def test_integration_guard_lists_setupable_ids_and_covered_evidence_kinds() -> None:
    harness_ports.set_setupable_integration_services(
        lambda: ("posthog_mcp", "grafana", "sentry_mcp")
    )
    harness_ports.clear_preferred_evidence_sources()
    harness_ports.register_preferred_evidence_source("metric_read", "posthog_mcp")

    text = _build_integration_guard(_snapshot(connected=("posthog_mcp", "grafana", "github")))

    assert "posthog_mcp" in text
    assert "Only these service ids are valid" in text
    assert "Never invent a service id that is not in that list" in text
    assert "mixpanel" not in text.lower()
    assert "evidence kind `metric_read`" in text
    assert "already connected" in text.lower()
    assert "That kind is covered" in text


def test_integration_guard_empty_when_integrations_unknown() -> None:
    assert _build_integration_guard(_snapshot(connected=(), known=False)) == ""


def test_integration_guard_keeps_an_empty_session_free_of_the_setup_id_roster() -> None:
    """An incident paste with nothing connected must not carry the vendor list.

    The roster is several hundred characters of vendor names. Emitting it on a
    session with no integrations pushed live oracle case
    ``307-new-alert-checkout-502`` into a generic setup offer that never named
    the failing service.
    """
    # Arrange
    harness_ports.set_setupable_integration_services(
        lambda: ("posthog_mcp", "grafana", "sentry_mcp")
    )

    # Act
    text = _build_integration_guard(_snapshot(connected=()))

    # Assert
    assert "Only these service ids are valid" not in text
    assert "posthog_mcp" not in text
    assert "/integrations setup" in text  # the plain guidance stays

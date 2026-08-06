"""Every vendor extension point is actually wired at startup.

The refactor that moved vendor-specific behavior out of ``core/`` replaced
direct imports with registries: integrations register prompt fragments, anchor
parsers, taxonomy profiles, alert-detail fields, and repo-scope providers from
``integrations/harness_adapters.py``. A vendor that stops registering does not
raise — it silently disappears from prompts and parsing. These tests fail
loudly instead, one assertion per registry.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import platform.harness_ports as harness_ports
from core.domain.alerts import extraction as alert_extraction
from core.domain.diagnosis import taxonomy_registry
from core.domain.types import incident_anchors
from surfaces.interactive_shell.ui.output import boundary as output_boundary


@pytest.fixture(autouse=True)
def _installed_ports() -> Iterator[None]:
    output_boundary.install_harness_ports()
    yield
    harness_ports.reset_harness_ports()


def test_gather_prompt_fragments_cover_every_registered_vendor() -> None:
    # Act
    fragments = harness_ports.gather_prompt_vendor_fragments()

    # Assert: one distinctive marker per vendor that owns a gather fragment.
    assert "github" in fragments.lower()
    assert "sentry" in fragments.lower()
    assert "slack" in fragments.lower()


def test_action_prompt_fragments_cover_every_registered_vendor() -> None:
    # Act
    fragments = harness_ports.action_prompt_vendor_fragments()

    # Assert
    assert "slack_read_messages" in fragments
    assert "github" in fragments.lower()
    assert "telegram" in fragments.lower()
    assert "rocketchat" in fragments.lower()


def test_assistant_prompt_fragments_cover_every_registered_vendor() -> None:
    # Act
    fragments = harness_ports.assistant_prompt_vendor_fragments()

    # Assert
    assert "sentry" in fragments.lower()
    assert "posthog" in fragments.lower()
    assert "slack" in fragments.lower()


def test_gateway_persona_fragment_is_registered() -> None:
    # Act
    persona = harness_ports.gateway_persona_fragments()

    # Assert: the Slack teammate persona wording core no longer owns.
    assert "AI production engineer" in persona
    assert "never call it the 'interactive shell'" in persona


def test_message_context_stripper_handles_slack_prefix() -> None:
    # Arrange
    text = "[Slack channel_id=C123 thread_ts=1.2] who is on the team?"

    # Act
    prefix, remainder = harness_ports.strip_message_context_prefix(text)

    # Assert
    assert prefix.startswith("[Slack")
    assert remainder == "who is on the team?"


def test_vcs_repo_scope_providers_registered_for_both_hosts() -> None:
    # Assert: GitHub and GitLab both register a scope provider.
    assert len(harness_ports._vcs_repo_scope_providers) == 2


def test_incident_anchor_parsers_are_registered() -> None:
    # Assert: alertmanager, pagerduty, datadog, cloudwatch.
    assert len(incident_anchors._anchor_parsers) == 4


def test_alert_detail_fields_registered_for_aws_and_kubernetes() -> None:
    # Act
    fields = alert_extraction.alert_detail_field_names()

    # Assert
    for field in ("cloudwatch_log_group", "eks_cluster", "kube_namespace", "pod_name"):
        assert field in fields


def test_taxonomy_profile_registered_for_hermes() -> None:
    # Act: hermes owns a scoped taxonomy; an unrelated source falls back.
    hermes = taxonomy_registry.taxonomy_categories_for_alert_source("hermes")
    other = taxonomy_registry.taxonomy_categories_for_alert_source("datadog")

    # Assert: the profile claims hermes and yields a different set than the default.
    assert hermes
    assert hermes != other


def test_reinstalling_ports_does_not_duplicate_fragments() -> None:
    # Arrange: capture the single-install prompt text.
    once = harness_ports.action_prompt_vendor_fragments()

    # Act: startup wiring may run more than once (REPL + gateway in one process).
    output_boundary.install_harness_ports()
    twice = harness_ports.action_prompt_vendor_fragments()

    # Assert: each _register_* clears first, so text is not doubled.
    assert twice == once


def test_reset_clears_every_vendor_registry() -> None:
    # Act
    harness_ports.reset_harness_ports()

    # Assert: nothing vendor-specific survives into the next test.
    assert harness_ports.gather_prompt_vendor_fragments() == ""
    assert harness_ports.action_prompt_vendor_fragments() == ""
    assert harness_ports.assistant_prompt_vendor_fragments() == ""
    assert harness_ports.gateway_persona_fragments() == ""
    assert harness_ports._vcs_repo_scope_providers == []

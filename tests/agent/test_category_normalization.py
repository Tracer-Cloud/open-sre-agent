from __future__ import annotations

from core.domain.diagnosis import (
    normalize_root_cause_category,
    root_cause_category_instruction_for_source,
    taxonomy_categories_for_alert_source,
)


def test_non_hermes_instruction_includes_full_taxonomy() -> None:
    instruction = root_cause_category_instruction_for_source("grafana")

    assert "Root cause category taxonomy" in instruction
    assert "connection_exhaustion" in instruction
    assert "[database]" in instruction
    assert "Hermes root cause category taxonomy" not in instruction


def test_hermes_instruction_includes_hermes_taxonomy_only() -> None:
    instruction = root_cause_category_instruction_for_source("hermes")

    assert "Hermes root cause category taxonomy" in instruction
    assert "agent_hang" in instruction
    assert "connection_exhaustion" not in instruction


def test_normalize_maps_legacy_coarse_bucket_to_canonical_category() -> None:
    allowed = taxonomy_categories_for_alert_source("grafana")

    assert normalize_root_cause_category("database", allowed_categories=allowed) == (
        "connection_exhaustion"
    )
    assert normalize_root_cause_category("memory_pressure", allowed_categories=allowed) == (
        "pod_oomkilled"
    )


def test_normalize_passthrough_for_canonical_category() -> None:
    allowed = taxonomy_categories_for_alert_source("grafana")

    assert (
        normalize_root_cause_category("connection_exhaustion", allowed_categories=allowed)
        == "connection_exhaustion"
    )


def test_normalize_maps_spacing_and_hyphen_variants() -> None:
    allowed = taxonomy_categories_for_alert_source("grafana")

    # "OOM Killed" -> token "oom_killed" -> alias -> pod_oomkilled
    assert normalize_root_cause_category("OOM Killed", allowed_categories=allowed) == (
        "pod_oomkilled"
    )
    assert normalize_root_cause_category("dns-failure", allowed_categories=allowed) == (
        "dns_resolution_failure"
    )


def test_normalize_fail_closed_maps_unknown_labels_to_unknown() -> None:
    allowed = taxonomy_categories_for_alert_source("grafana")

    assert (
        normalize_root_cause_category("totally_unknown_label", allowed_categories=allowed)
        == "unknown"
    )


def test_normalize_fail_closed_when_alias_target_out_of_scope() -> None:
    """Alias targets outside the allowed taxonomy must not leak through."""
    hermes_allowed = {"agent_hang", "ghost_session", "unknown"}

    assert normalize_root_cause_category("database", allowed_categories=hermes_allowed) == (
        "unknown"
    )
    assert (
        normalize_root_cause_category(
            "performance_degradation",
            allowed_categories=hermes_allowed,
        )
        == "unknown"
    )

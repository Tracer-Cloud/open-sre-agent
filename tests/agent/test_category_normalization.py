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


def test_normalize_leaves_unknown_labels_unchanged() -> None:
    allowed = taxonomy_categories_for_alert_source("grafana")

    assert (
        normalize_root_cause_category("totally_unknown_label", allowed_categories=allowed)
        == "totally_unknown_label"
    )


def test_normalize_respects_allowed_category_scope() -> None:
    hermes_allowed = taxonomy_categories_for_alert_source("hermes")

    # database -> connection_exhaustion only when that target is in the allowed set
    assert (
        normalize_root_cause_category("database", allowed_categories=hermes_allowed) == "database"
    )
    assert (
        normalize_root_cause_category(
            "performance_degradation",
            allowed_categories=hermes_allowed,
        )
        == "performance_degradation"
    )

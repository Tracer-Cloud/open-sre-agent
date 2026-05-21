"""Shared policy/signal tags across routing command and agent paths."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class RouteSignal(StrEnum):
    """Top-level routing matched-signal tags."""

    SLASH_PREFIX = "slash_prefix"
    BARE_COMMAND_ALIAS = "bare_command_alias"
    OPENSRE_INVESTIGATE = "opensre_investigate"
    CLI_AGENT_ACTION_PLAN = "cli_agent_action_plan"
    CLI_AGENT_DEGRADED = "cli_agent_degraded"


class DeterministicMapperPolicyTag(StrEnum):
    """Policy tags emitted by deterministic clause mapping."""

    UNHANDLED_CLAUSE_DETECTED = "unhandled_clause_detected"
    TEXT_LEVEL_INVESTIGATION_ADDED = "text_level_investigation_added"
    INVESTIGATION_ONLY_UNHANDLED_WAIVED = "investigation_only_unhandled_waived"


class ClauseMappingPhaseTag(StrEnum):
    """Ordered clause-mapping phases for deterministic routing."""

    SYNTHETIC = "synthetic"
    REGISTRY_AND_INTEGRATION_DETAIL = "registry_and_integration_detail"
    FALLBACK_EXTRACTORS = "fallback_extractors"


class PlannerPostprocessPolicyTag(StrEnum):
    """Policy identifiers for planner postprocessing decisions."""

    FAIL_CLOSED_VAGUE_LOCAL_MODEL = "fail_closed_vague_local_model"
    FAIL_CLOSED_UNCONFIGURED_INTEGRATION_DETAIL = "fail_closed_unconfigured_integration_detail"
    RECONCILE_COMPOUND_WITH_DETERMINISTIC = "reconcile_compound_with_deterministic"
    UPGRADE_HANDOFF_TO_INCIDENT = "upgrade_handoff_to_incident"
    COERCE_INCIDENT_PASTE_HANDOFF = "coerce_incident_paste_handoff"
    FAIL_CLOSED_AFTER_POLICY = "fail_closed_after_policy"


def encode_policy_trace(tags: Iterable[StrEnum | str]) -> str:
    """Stable comma-delimited policy trace for analytics payloads."""
    return ",".join(str(tag.value if isinstance(tag, StrEnum) else tag) for tag in tags)


__all__ = [
    "ClauseMappingPhaseTag",
    "DeterministicMapperPolicyTag",
    "PlannerPostprocessPolicyTag",
    "RouteSignal",
    "encode_policy_trace",
]

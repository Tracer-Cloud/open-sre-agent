"""Semantic evidence source predicates for the synthetic RDS benchmark suite.

Each evidence source has a canonical ID (matching fixture schema keys) and a
predicate that inspects ``final_state["evidence"]`` to determine whether the
agent actually gathered that source's data.

The key design constraint: ``aws_cloudwatch_metrics`` and
``aws_performance_insights`` must NOT be conflated.  Both may appear in
``grafana_metrics`` at the transport layer, but they are semantically distinct:
CloudWatch carries time-series metrics; Performance Insights carries DB-load
attribution with top SQL, wait events, and AAS data.

Predicates are pure functions (no I/O, no LLM calls) and can be unit-tested
without importing heavy runtime dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EvidenceSourceId(StrEnum):
    """Canonical IDs matching ``VALID_EVIDENCE_SOURCES`` in ``tests/synthetic/schemas.py``."""

    AWS_CLOUDWATCH_METRICS = "aws_cloudwatch_metrics"
    AWS_RDS_EVENTS = "aws_rds_events"
    AWS_PERFORMANCE_INSIGHTS = "aws_performance_insights"
    EC2_INSTANCES_BY_TAG = "ec2_instances_by_tag"
    ELB_TARGET_HEALTH = "elb_target_health"
    K8S_EVENTS = "k8s_events"
    K8S_POD_METRICS = "k8s_pod_metrics"
    K8S_NODE_METRICS = "k8s_node_metrics"
    K8S_DNS_METRICS = "k8s_dns_metrics"
    K8S_MESH_METRICS = "k8s_mesh_metrics"
    K8S_ROLLOUT = "k8s_rollout"


@dataclass(frozen=True)
class EvidencePresence:
    source_id: str
    present: bool
    reason: str


# ---------------------------------------------------------------------------
# PI signal tokens: these appear only in Performance Insights data, not in
# raw CloudWatch time-series.  Used as a secondary check when the agent
# stores PI data under the grafana_metrics key without a separate semantic key.
# ---------------------------------------------------------------------------

_PI_SIGNAL_TOKENS = frozenset(
    {
        "top sql activity",
        "avg load",
        "aas",
        "active sessions",
        "db load",
        "walwrite",
        "clientread",
        "top_sql",
        "top_wait_events",
        "wait_events",
    }
)


def _has_pi_signals(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _PI_SIGNAL_TOKENS)


def _evidence_cloudwatch(evidence: dict[str, Any]) -> EvidencePresence:
    """CloudWatch is present when the agent populated its dedicated evidence key."""
    raw = evidence.get("aws_cloudwatch_metrics")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.AWS_CLOUDWATCH_METRICS,
            present=True,
            reason="aws_cloudwatch_metrics key populated in evidence",
        )
    # Fallback: grafana_metrics populated but no PI-typed signals → CloudWatch-only data.
    grafana = evidence.get("grafana_metrics")
    if grafana:
        import json as _json

        grafana_text = _json.dumps(grafana, default=str)
        if not _has_pi_signals(grafana_text):
            return EvidencePresence(
                source_id=EvidenceSourceId.AWS_CLOUDWATCH_METRICS,
                present=True,
                reason="grafana_metrics populated with non-PI signals (inferred CloudWatch)",
            )
    return EvidencePresence(
        source_id=EvidenceSourceId.AWS_CLOUDWATCH_METRICS,
        present=False,
        reason="no CloudWatch evidence found in aws_cloudwatch_metrics or grafana_metrics",
    )


def _evidence_rds_events(evidence: dict[str, Any]) -> EvidencePresence:
    """RDS events are present when the agent populated grafana_logs or aws_rds_events."""
    if evidence.get("grafana_logs"):
        return EvidencePresence(
            source_id=EvidenceSourceId.AWS_RDS_EVENTS,
            present=True,
            reason="grafana_logs populated (RDS events transported via Grafana logs channel)",
        )
    if evidence.get("aws_rds_events"):
        return EvidencePresence(
            source_id=EvidenceSourceId.AWS_RDS_EVENTS,
            present=True,
            reason="aws_rds_events key populated in evidence",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.AWS_RDS_EVENTS,
        present=False,
        reason="no RDS events found in grafana_logs or aws_rds_events",
    )


def _evidence_performance_insights(
    evidence: dict[str, Any],
    output_text: str = "",
) -> EvidencePresence:
    """Performance Insights is present when the agent populated the PI evidence key
    with meaningful data (top_sql or top_wait_events) OR when PI-typed signals appear
    in the agent's reasoning output.

    Critically: a populated ``grafana_metrics`` key alone is NOT sufficient — that
    could be pure CloudWatch data.
    """
    pi_raw = evidence.get("aws_performance_insights")
    if pi_raw and isinstance(pi_raw, dict):
        has_top_sql = bool(pi_raw.get("top_sql"))
        has_wait_events = bool(pi_raw.get("top_wait_events") or pi_raw.get("wait_events"))
        has_db_load = bool(pi_raw.get("db_load"))
        observations = " ".join(pi_raw.get("observations") or [])
        has_pi_obs = _has_pi_signals(observations)
        if has_top_sql or has_wait_events or has_db_load or has_pi_obs:
            return EvidencePresence(
                source_id=EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS,
                present=True,
                reason="aws_performance_insights key populated with PI-typed data",
            )

    # Secondary: PI tokens in grafana_metrics (agent merged data without a dedicated key)
    grafana = evidence.get("grafana_metrics")
    if grafana:
        import json as _json

        grafana_text = _json.dumps(grafana, default=str)
        if _has_pi_signals(grafana_text):
            return EvidencePresence(
                source_id=EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS,
                present=True,
                reason="PI-typed signals detected in grafana_metrics content",
            )

    # Tertiary: PI tokens in agent reasoning output (e.g. report text)
    if output_text and _has_pi_signals(output_text):
        return EvidencePresence(
            source_id=EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS,
            present=True,
            reason="PI-typed signals detected in agent reasoning output",
        )

    return EvidencePresence(
        source_id=EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS,
        present=False,
        reason=(
            "no Performance Insights signal found: aws_performance_insights key absent or empty, "
            "grafana_metrics has no PI tokens, reasoning output has no PI tokens"
        ),
    )


def _evidence_ec2_instances_by_tag(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("ec2_instances_by_tag")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.EC2_INSTANCES_BY_TAG,
            present=True,
            reason="ec2_instances_by_tag key populated in evidence",
        )
    # Runtime mapper shape from app.nodes.investigate.processing.post_process.
    if evidence.get("ec2_instances") or evidence.get("ec2_instances_by_tier"):
        return EvidencePresence(
            source_id=EvidenceSourceId.EC2_INSTANCES_BY_TAG,
            present=True,
            reason="ec2_instances/ec2_instances_by_tier populated (mapped ec2_instances_by_tag action)",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.EC2_INSTANCES_BY_TAG,
        present=False,
        reason="no ec2_instances_by_tag evidence found",
    )


def _evidence_elb_target_health(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("elb_target_health")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.ELB_TARGET_HEALTH,
            present=True,
            reason="elb_target_health key populated in evidence",
        )
    # Runtime mapper shape from app.nodes.investigate.processing.post_process.
    if (
        evidence.get("elb_target_groups")
        or evidence.get("elb_healthy_targets")
        or evidence.get("elb_unhealthy_targets")
        or evidence.get("elb_target_health_summary")
    ):
        return EvidencePresence(
            source_id=EvidenceSourceId.ELB_TARGET_HEALTH,
            present=True,
            reason="elb_target_* keys populated (mapped get_elb_target_health action)",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.ELB_TARGET_HEALTH,
        present=False,
        reason="no elb_target_health evidence found",
    )


def _evidence_k8s_events(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("k8s_events")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.K8S_EVENTS,
            present=True,
            reason="k8s_events key populated in evidence",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.K8S_EVENTS,
        present=False,
        reason="no k8s_events evidence found",
    )


def _evidence_k8s_pod_metrics(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("k8s_pod_metrics")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.K8S_POD_METRICS,
            present=True,
            reason="k8s_pod_metrics key populated in evidence",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.K8S_POD_METRICS,
        present=False,
        reason="no k8s_pod_metrics evidence found",
    )


def _evidence_k8s_node_metrics(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("k8s_node_metrics")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.K8S_NODE_METRICS,
            present=True,
            reason="k8s_node_metrics key populated in evidence",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.K8S_NODE_METRICS,
        present=False,
        reason="no k8s_node_metrics evidence found",
    )


def _evidence_k8s_dns_metrics(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("k8s_dns_metrics")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.K8S_DNS_METRICS,
            present=True,
            reason="k8s_dns_metrics key populated in evidence",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.K8S_DNS_METRICS,
        present=False,
        reason="no k8s_dns_metrics evidence found",
    )


def _evidence_k8s_mesh_metrics(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("k8s_mesh_metrics")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.K8S_MESH_METRICS,
            present=True,
            reason="k8s_mesh_metrics key populated in evidence",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.K8S_MESH_METRICS,
        present=False,
        reason="no k8s_mesh_metrics evidence found",
    )


def _evidence_k8s_rollout(evidence: dict[str, Any]) -> EvidencePresence:
    raw = evidence.get("k8s_rollout")
    if raw:
        return EvidencePresence(
            source_id=EvidenceSourceId.K8S_ROLLOUT,
            present=True,
            reason="k8s_rollout key populated in evidence",
        )
    return EvidencePresence(
        source_id=EvidenceSourceId.K8S_ROLLOUT,
        present=False,
        reason="no k8s_rollout evidence found",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_PREDICATES = {
    EvidenceSourceId.AWS_CLOUDWATCH_METRICS: _evidence_cloudwatch,
    EvidenceSourceId.AWS_RDS_EVENTS: _evidence_rds_events,
    EvidenceSourceId.EC2_INSTANCES_BY_TAG: _evidence_ec2_instances_by_tag,
    EvidenceSourceId.ELB_TARGET_HEALTH: _evidence_elb_target_health,
    EvidenceSourceId.K8S_EVENTS: _evidence_k8s_events,
    EvidenceSourceId.K8S_POD_METRICS: _evidence_k8s_pod_metrics,
    EvidenceSourceId.K8S_NODE_METRICS: _evidence_k8s_node_metrics,
    EvidenceSourceId.K8S_DNS_METRICS: _evidence_k8s_dns_metrics,
    EvidenceSourceId.K8S_MESH_METRICS: _evidence_k8s_mesh_metrics,
    EvidenceSourceId.K8S_ROLLOUT: _evidence_k8s_rollout,
}


def evaluate(
    final_state: dict[str, Any],
    required_source_ids: list[str],
) -> list[EvidencePresence]:
    """Evaluate which of *required_source_ids* are present in *final_state*.

    Args:
        final_state: The agent's completed investigation state dict.
        required_source_ids: Semantic source ID strings from
            ``ScenarioAnswerKey.required_evidence_sources``.

    Returns:
        One ``EvidencePresence`` per required source, in input order.
    """
    evidence = final_state.get("evidence") or {}
    output_text = _build_output_text(final_state)
    results: list[EvidencePresence] = []
    for source_id_str in required_source_ids:
        try:
            source_id = EvidenceSourceId(source_id_str)
        except ValueError:
            results.append(
                EvidencePresence(
                    source_id=source_id_str,
                    present=False,
                    reason=f"unknown source id {source_id_str!r}",
                )
            )
            continue

        if source_id == EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS:
            results.append(_evidence_performance_insights(evidence, output_text))
        else:
            predicate = _PREDICATES.get(source_id)
            if predicate is None:
                results.append(
                    EvidencePresence(
                        source_id=source_id_str,
                        present=False,
                        reason=f"no predicate registered for {source_id_str!r}",
                    )
                )
            else:
                results.append(predicate(evidence))
    return results


def missing_sources(
    final_state: dict[str, Any],
    required_source_ids: list[str],
) -> list[str]:
    """Return the subset of *required_source_ids* that are absent from *final_state*.

    Returns plain ``str`` values (not enum instances) so they format cleanly in
    f-strings and list reprs without ``<EvidenceSourceId...>`` noise.
    """
    return [
        presence.source_id.value
        if isinstance(presence.source_id, EvidenceSourceId)
        else str(presence.source_id)
        for presence in evaluate(final_state, required_source_ids)
        if not presence.present
    ]


def _build_output_text(final_state: dict[str, Any]) -> str:
    """Concatenate agent reasoning text for secondary PI signal detection."""
    parts = [
        str(final_state.get("root_cause") or ""),
        " ".join(c.get("claim", "") for c in (final_state.get("validated_claims") or [])),
        " ".join(c.get("claim", "") for c in (final_state.get("non_validated_claims") or [])),
        " ".join(final_state.get("causal_chain") or []),
        str(final_state.get("report") or ""),
        str((final_state.get("problem_report") or {}).get("report_md") or ""),
    ]
    return " ".join(parts)

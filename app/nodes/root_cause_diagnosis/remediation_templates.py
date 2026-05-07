"""Deterministic remediation step fallbacks keyed on root_cause_category."""

from __future__ import annotations

from collections.abc import Mapping

_TEMPLATES: dict[str, list[tuple[str, str | None]]] = {
    "resource_exhaustion": [
        (
            "Identify the saturated resource (memory, CPU, connections, storage) from the evidence",
            None,
        ),
        ("Scale up or right-size the affected workload or database", None),
        ("Set resource limits and alerts at 80% to catch saturation early", None),
        ("Review Grafana dashboards for resource trend leading up to the incident", "grafana"),
        ("Check Datadog monitors for threshold breaches on the affected resource", "datadog"),
        ("List EKS pods and confirm OOMKill events with kubectl describe", "eks"),
    ],
    "dependency_failure": [
        ("Identify the failing upstream service or dependency from error logs", None),
        ("Check upstream service health page and recent deployments", None),
        ("Enable circuit breaker or retry with exponential backoff if not active", None),
        ("Review Grafana logs for connection errors or timeouts to the dependency", "grafana"),
        ("Check Datadog monitors for upstream SLO breach", "datadog"),
    ],
    "configuration_error": [
        (
            "Diff the configuration deployed before the incident against the last known-good config",
            None,
        ),
        ("Roll back the configuration change that introduced the mismatch", None),
        ("Add validation checks to CI/CD pipeline for configuration values", None),
    ],
    "code_defect": [
        (
            "Identify the commit introducing the defect using git history or recent deploy timestamps",
            None,
        ),
        ("Roll back or hot-fix the affected service", None),
        ("Add a regression test covering the failing code path before re-deploying", None),
    ],
    "data_quality": [
        ("Quarantine or skip the malformed records to unblock the pipeline", None),
        ("Add schema validation at the ingestion boundary", None),
        ("Trace the upstream source of the bad data and notify the owner", None),
    ],
    "infrastructure": [
        ("Check cloud provider status page and recent AWS service events for the region", None),
        ("Verify IAM roles, VPC security groups, and networking rules are unchanged", None),
        ("Trigger failover to standby if the primary zone is degraded", None),
    ],
    "unknown": [
        ("Enable debug logging and re-run the failing workload to gather more signal", None),
        (
            "Escalate to the owning team with the investigation trace and causal chain attached",
            None,
        ),
    ],
    "healthy": [],
}


def get_template_steps(category: str, available_sources: Mapping[str, object]) -> list[str]:
    """Return filtered remediation steps for the given root_cause_category."""
    entries = _TEMPLATES.get(category, _TEMPLATES["unknown"])
    return [
        step
        for step, required_source in entries
        if required_source is None or required_source in available_sources
    ]

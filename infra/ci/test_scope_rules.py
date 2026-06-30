"""Path → pytest target mapping for branch-scoped test runs (CI.md §2).

This module is the single source of truth for ``make test-scope``. Edit rules
here only — do not duplicate the mapping table in CI.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Distinct app areas in one diff that trigger escalation to ``make test-cov``.
ESCALATION_AREA_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class PathRule:
    """Map changed paths under ``path_prefix`` to pytest targets."""

    path_prefix: str
    test_targets: tuple[str, ...]
    always_escalate: bool = False


# Matched in list order — more specific prefixes must appear before parents.
RULES: tuple[PathRule, ...] = (
    # Shared core (always escalate)
    PathRule("core/domain/", (), always_escalate=True),
    PathRule("core/", ("tests/core/",)),
    PathRule("tools/investigation/reporting/", ("tests/delivery/",)),
    PathRule("tools/investigation/", (), always_escalate=True),
    PathRule("utils/", (), always_escalate=True),
    # Specific sub-packages before their parent
    PathRule("integrations/llm_cli/", ("tests/integrations/llm_cli/",)),
    PathRule("integrations/opensre/", ("tests/integrations/opensre/",)),
    PathRule("integrations/hermes/", ("tests/hermes/",)),
    PathRule(
        "integrations/alertmanager/",
        ("tests/integrations/alertmanager/", "tests/e2e/alertmanager/"),
    ),
    PathRule(
        "integrations/dagster/",
        ("tests/integrations/dagster/", "tests/synthetic/test_dagster_scenario.py"),
    ),
    PathRule(
        "integrations/eks/",
        (
            "tests/integrations/eks/",
            "tests/tools/test_eks_deployment_status_tool.py",
            "tests/tools/test_eks_describe_addon_tool.py",
            "tests/tools/test_eks_describe_cluster_tool.py",
            "tests/tools/test_eks_events_tool.py",
            "tests/tools/test_eks_list_clusters_tool.py",
            "tests/tools/test_eks_list_deployments_tool.py",
            "tests/tools/test_eks_list_namespaces_tool.py",
            "tests/tools/test_eks_list_pods_tool.py",
            "tests/tools/test_eks_node_health_tool.py",
            "tests/tools/test_eks_nodegroup_health_tool.py",
            "tests/tools/test_eks_pod_logs_tool.py",
            "tests/tools/test_telemetry.py",
            "tests/benchmarks/cloudopsbench/tests/test_bench_agent.py",
        ),
    ),
    PathRule(
        "integrations/elasticsearch/",
        (
            "tests/integrations/elasticsearch/",
            "tests/tools/test_elasticsearch_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/google_docs/",
        (
            "tests/integrations/google_docs/",
            "tests/test_google_docs.py",
            "tests/tools/test_google_docs_create_report_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/groundcover/",
        ("tests/integrations/groundcover/", "tests/tools/test_groundcover_tools.py"),
    ),
    PathRule(
        "integrations/helm/",
        ("tests/integrations/helm/", "tests/tools/test_helm_tools.py"),
    ),
    PathRule(
        "integrations/incident_io/",
        ("tests/integrations/incident_io/", "tests/tools/test_incident_io_tool.py"),
    ),
    PathRule(
        "integrations/jira/",
        (
            "tests/integrations/jira/",
            "tests/tools/test_jira_add_comment_tool.py",
            "tests/tools/test_jira_create_issue_tool.py",
            "tests/tools/test_jira_issue_detail_tool.py",
            "tests/tools/test_jira_search_issues_tool.py",
        ),
    ),
    PathRule(
        "integrations/opsgenie/",
        (
            "tests/integrations/opsgenie/",
            "tests/tools/test_opsgenie_alert_detail_tool.py",
            "tests/tools/test_opsgenie_alerts_tool.py",
        ),
    ),
    PathRule(
        "integrations/pagerduty/",
        (
            "tests/integrations/pagerduty/",
            "tests/tools/test_pagerduty_incident_detail_tool.py",
            "tests/tools/test_pagerduty_incidents_tool.py",
            "tests/tools/test_pagerduty_oncall_tool.py",
            "tests/tools/test_pagerduty_services_tool.py",
        ),
    ),
    PathRule(
        "integrations/prefect/",
        (
            "tests/integrations/prefect/",
            "tests/tools/test_prefect_flow_runs_tool.py",
            "tests/tools/test_prefect_worker_health_tool.py",
        ),
    ),
    PathRule(
        "integrations/signoz/",
        (
            "tests/integrations/signoz/",
            "tests/tools/test_signoz_tools.py",
            "tests/synthetic/test_signoz_scenario.py",
        ),
    ),
    PathRule(
        "integrations/splunk/",
        ("tests/integrations/splunk/", "tests/tools/test_splunk_search_tool.py"),
    ),
    PathRule(
        "integrations/tempo/",
        (
            "tests/integrations/tempo/",
            "tests/tools/test_tempo_tools.py",
            "tests/synthetic/test_tempo_scenario.py",
        ),
    ),
    PathRule(
        "integrations/temporal/",
        (
            "tests/integrations/temporal/",
            "tests/integrations/test_temporal_catalog.py",
            "tests/synthetic/test_temporal_scenario.py",
            "tests/tools/test_temporal_namespace_info_tool.py",
            "tests/tools/test_temporal_task_queue_tool.py",
            "tests/tools/test_temporal_workflow_history_tool.py",
            "tests/tools/test_temporal_workflows_tool.py",
        ),
    ),
    PathRule(
        "integrations/vercel/",
        (
            "tests/integrations/vercel/",
            "tests/tools/test_vercel_deployment_status_tool.py",
            "tests/tools/test_vercel_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/victoria_logs/",
        (
            "tests/integrations/victoria_logs/",
            "tests/tools/test_victoria_logs_tool.py",
            "tests/e2e/victoria_logs/",
        ),
    ),
    PathRule(
        "integrations/argocd/",
        (
            "tests/integrations/argocd/",
            "tests/tools/test_argocd_tools.py",
        ),
    ),
    PathRule(
        "integrations/coralogix/",
        (
            "tests/integrations/coralogix/",
            "tests/tools/test_coralogix_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/honeycomb/",
        (
            "tests/integrations/honeycomb/",
            "tests/tools/test_honeycomb_traces_tool.py",
        ),
    ),
    PathRule(
        "integrations/jenkins/",
        ("tests/integrations/test_jenkins.py", "tests/synthetic/test_jenkins_scenario.py"),
    ),
    PathRule(
        "integrations/datadog/",
        (
            "tests/integrations/datadog/",
            "tests/tools/test_datadog_context_tool.py",
            "tests/tools/test_datadog_events_tool.py",
            "tests/tools/test_datadog_logs_tool.py",
            "tests/tools/test_datadog_metrics_tool.py",
            "tests/tools/test_datadog_monitors_tool.py",
            "tests/tools/test_datadog_node_pods_tool.py",
        ),
    ),
    PathRule(
        "integrations/grafana/",
        (
            "tests/integrations/grafana/",
            "tests/tools/test_grafana_alert_rules_tool.py",
            "tests/tools/test_grafana_annotations_tool.py",
            "tests/tools/test_grafana_logs_tool.py",
            "tests/tools/test_grafana_metrics_tool.py",
            "tests/tools/test_grafana_service_names_tool.py",
            "tests/tools/test_grafana_traces_tool.py",
            "tests/e2e/grafana_validation/",
        ),
    ),
    PathRule("integrations/", ("tests/integrations/",)),
    PathRule("tools/fleet_monitoring/", ("tests/agent/", "tests/fleet_monitoring/")),
    PathRule("surfaces/cli/", ("tests/cli/",)),
    PathRule("surfaces/interactive_shell/", ("tests/interactive_shell/",)),
    PathRule("gateway/", ("gateway/tests/",)),
    PathRule("tools/watch_dog/", ("tests/watch_dog/",)),
    PathRule("tools/", ("tests/tools/",)),
    PathRule("platform/analytics/", ("tests/analytics/",)),
    PathRule("platform/guardrails/", ("tests/test_guardrails/",)),
    PathRule("platform/masking/", ("tests/masking/",)),
    PathRule("infra/deployment/entrypoints/", ("tests/entrypoints/",)),
    PathRule("infra/deployment/remote/", ("tests/remote/",)),
    PathRule("platform/sandbox/", ("tests/sandbox/",)),
    PathRule("infra/deployment/", ("tests/deployment/",)),
    PathRule("platform/auth/", ("tests/platform/auth/",)),
    PathRule("config/webapp.py", ("tests/test_webapp.py",)),
    # Repo-wide config
    PathRule("pyproject.toml", (), always_escalate=True),
    PathRule("uv.lock", (), always_escalate=True),
    PathRule("pytest.ini", (), always_escalate=True),
    PathRule("Makefile", (), always_escalate=True),
    PathRule("infra/ci/", ("tests/infra_ci/",)),
)


def _matches(path: str, prefix: str) -> bool:
    return path.startswith(prefix) or path == prefix.rstrip("/")


def _area_key(prefix: str) -> str:
    parts = prefix.split("/")
    if parts[0] == "deployment" or parts[:2] == ["infra", "deployment"]:
        return "deployment"
    return prefix


def classify(changed: list[str]) -> tuple[bool, list[str], list[str]]:
    """Return ``(should_escalate, test_targets, matched_areas)``."""
    escalate = False
    targets: list[str] = []
    areas: list[str] = []

    for path in changed:
        matched = False
        for rule in RULES:
            if not _matches(path, rule.path_prefix):
                continue
            matched = True
            if rule.always_escalate:
                escalate = True
            else:
                area = _area_key(rule.path_prefix)
                if area not in areas:
                    areas.append(area)
                for target in rule.test_targets:
                    if target not in targets:
                        targets.append(target)
            break

        if not matched and path.startswith("tests/") and path not in targets:
            targets.append(path)

    if len(areas) >= ESCALATION_AREA_THRESHOLD:
        escalate = True

    existing = [t for t in targets if Path(t).exists()]
    dropped = [t for t in targets if t not in existing]
    if dropped:
        print(f"  (skipping non-existent targets: {', '.join(dropped)})", flush=True)
    return escalate, existing, areas

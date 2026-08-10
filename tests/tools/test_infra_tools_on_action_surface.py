"""Tests for infrastructure tools on the action surface.

Verifies that exactly the curated set of 18 kubernetes/gcp tools are available
on the action surface, with sensitive tools excluded and credentials properly
protected.
"""

import pytest
from rich.console import Console

from core.agent_harness.tools.action_tools import (
    availability_view,
    get_action_tools_from_integrations_context,
)
from core.agent_harness.tools.tool_context import ActionToolContext
from tools.registry import get_registered_tool_map, get_registered_tools

# The exact set of 18 tools that should be on the action surface
EXPECTED_INFRA_TOOLS_ON_ACTION = {
    # Kubernetes tools (10)
    "kubernetes_list_pods",
    "kubernetes_get_pod_logs",
    "kubernetes_list_workloads",
    "kubernetes_get_events",
    "kubernetes_describe_pod",
    "kubernetes_list_namespaces",
    "kubernetes_list_nodes",
    "kubernetes_list_services",
    "kubernetes_list_clusters",
    "kubernetes_search_fleet",
    # GCP tools (8)
    "gcp_list_projects",
    "gcp_list_gke_clusters",
    "gcp_monitoring_query",
    "gcp_error_reporting_top_errors",
    "gcp_refresh_discovery",
    "gcp_logging_query",
    "gcp_metrics_scope",
    "gcp_alerting",
}

# Tools that should be excluded from action surface for security reasons
EXCLUDED_INFRA_TOOLS = {
    "kubernetes_list_statefulsets",
    "kubernetes_list_daemonsets",
    "kubernetes_list_ingresses",
    "kubernetes_list_configmaps",
    "kubernetes_list_deployments",
    "kubernetes_list_rollouts",
    "kubernetes_get_resource",
    "gcp_audit_log_query",
    "gcp_list_compute_instances",
    "gcp_list_cloud_run_services",
    "gcp_list_cloud_sql_instances",
    "gcp_pubsub_backlog",
}

# Kubernetes tools that take a cluster parameter and need credential protection
K8S_TOOLS_WITH_CLUSTER_PARAM = {
    "kubernetes_list_pods",
    "kubernetes_get_pod_logs",
    "kubernetes_list_workloads",
    "kubernetes_get_events",
    "kubernetes_describe_pod",
    "kubernetes_list_namespaces",
    "kubernetes_list_nodes",
    "kubernetes_list_services",
    "kubernetes_search_fleet",
}


def test_action_surface_infra_tools_are_exactly_the_curated_set():
    """Test that action surface has exactly the 18 curated infrastructure tools."""
    action_tools = get_registered_tools("action")
    actual_infra_tools = {
        tool.name for tool in action_tools if tool.name.startswith(("kubernetes_", "gcp_"))
    }

    assert actual_infra_tools == EXPECTED_INFRA_TOOLS_ON_ACTION


# The sets above are the right structure for the membership and equality
# assertions, but a set is the wrong thing to hand to parametrize: xdist runs
# each worker in its own process with its own PYTHONHASHSEED, so the workers
# derive different test ids from the same set and the run aborts with
# "Different tests were collected between gw0 and gw1" before anything executes.
# Sorting fixes the iteration order without changing the assertions.
@pytest.mark.parametrize("tool_name", sorted(EXCLUDED_INFRA_TOOLS))
def test_sensitive_infra_tools_are_not_on_the_action_surface(tool_name):
    """Test that sensitive tools are excluded from action but still on investigation."""
    # Tool should be absent from action surface
    action_tools = get_registered_tools("action")
    action_tool_names = {tool.name for tool in action_tools}
    assert tool_name not in action_tool_names

    # Tool should still be present on investigation surface
    investigation_tools = get_registered_tools("investigation")
    investigation_tool_names = {tool.name for tool in investigation_tools}
    assert tool_name in investigation_tool_names


@pytest.mark.parametrize("tool_name", sorted(K8S_TOOLS_WITH_CLUSTER_PARAM))
def test_cluster_credentials_stay_model_protected_on_action_tools(tool_name):
    """Test that kubernetes tools with cluster params have credential protection."""
    tool_map = get_registered_tool_map()
    tool = tool_map[tool_name]

    # cluster_configs should be in injected_params to prevent model override
    assert "cluster_configs" in tool.injected_params


def test_kubernetes_list_clusters_has_clusters_param_protected():
    """Test that kubernetes_list_clusters has clusters param protected."""
    tool_map = get_registered_tool_map()
    tool = tool_map["kubernetes_list_clusters"]

    # clusters should be in injected_params to prevent model override
    assert "clusters" in tool.injected_params


def _fake_session():
    """Build a fake session for testing."""
    from surfaces.interactive_shell.session.session import Session

    session = Session()
    return session


def _fake_resolved_integrations():
    """Build a fake resolved integrations dict with two clusters and gcp.

    The instance entries use the ``{name, tags, config}`` shape published by
    ``classify_integrations`` and required by ``integrations/selectors.py`` —
    a flat entry silently resolves to zero clusters rather than raising, so a
    malformed fixture makes the multi-cluster assertions below pass vacuously.
    """
    return {
        "kubernetes": {
            "kubeconfig": "fake-kubeconfig",
            "context": "gke-a",
            "namespace": "default",
        },
        "_all_kubernetes_instances": [
            {
                "name": "gke-a",
                "tags": {},
                "config": {
                    "kubeconfig": "fake-kubeconfig-a",
                    "context": "gke-a",
                    "namespace": "default",
                },
            },
            {
                "name": "gke-b",
                "tags": {},
                "config": {
                    "kubeconfig": "fake-kubeconfig-b",
                    "context": "gke-b",
                    "namespace": "production",
                },
            },
        ],
        "gcp": {
            "project_id": "opensre-test-project",
            "credentials": "fake-credentials",
        },
    }


def test_infra_tools_resolve_credentials_through_the_action_context():
    """Test that tools resolve credentials through action surface context."""
    fake_integrations = _fake_resolved_integrations()
    fake_session = _fake_session()

    # Create action tool context like the real surface does
    ctx = ActionToolContext(session=fake_session, console=Console(force_terminal=False))

    # Get action tools using the real function
    action_tools = get_action_tools_from_integrations_context(
        ctx, resolved_integrations=fake_integrations
    )

    # All 18 must resolve, not just the kubernetes half. A gcp availability
    # regression on this surface is caught here and nowhere else: registry
    # membership stays green while the model sees no gcp tool at all.
    resolved_infra = {
        tool.name for tool in action_tools if tool.name.startswith(("kubernetes_", "gcp_"))
    }
    assert resolved_infra == EXPECTED_INFRA_TOOLS_ON_ACTION

    # Build sources dict like the action surface does
    sources = availability_view(fake_integrations)
    sources["_action_session"] = ctx

    tool_map = get_registered_tool_map()
    pods_tool = tool_map["kubernetes_list_pods"]
    assert pods_tool.is_available(sources)

    # Both clusters must reach the tool, with connection fields intact — the
    # point of cluster_configs is that the model names a cluster and the
    # runtime supplies the credentials for it.
    cluster_configs = pods_tool.extract_params(sources)["cluster_configs"]
    assert set(cluster_configs) == {"gke-a", "gke-b"}
    assert cluster_configs["gke-b"]["namespace"] == "production"

    clusters = tool_map["kubernetes_list_clusters"].extract_params(sources)["clusters"]
    assert [entry["name"] for entry in clusters] == ["gke-a", "gke-b"]

"""Verify all EKS tools survive the split and register correctly.

After moving tools out of ``integrations/eks/tools/__init__.py`` into their
own modules, this test asserts that every tool's ``@tool`` decorator still
fires on import and the tool names are reachable from the package.
"""

from __future__ import annotations


def test_all_eks_tools_registered() -> None:
    import integrations.eks.tools as eks_tools  # triggers all @tool decorators

    expected_names = {
        "get_eks_deployment_status",
        "describe_eks_addon",
        "describe_eks_cluster",
        "get_eks_events",
        "list_eks_clusters",
        "list_eks_deployments",
        "list_eks_namespaces",
        "list_eks_pods",
        "get_eks_node_health",
        "get_eks_nodegroup_health",
        "get_eks_pod_logs",
    }
    # Each @tool-decorated function gets __opensre_registered_tool__ attached.
    registered: set[str] = set()
    for name in dir(eks_tools):
        obj = getattr(eks_tools, name)
        rt = getattr(obj, "__opensre_registered_tool__", None)
        if rt is not None:
            registered.add(rt.name)

    missing = expected_names - registered
    assert not missing, f"EKS tools missing after split: {sorted(missing)}"

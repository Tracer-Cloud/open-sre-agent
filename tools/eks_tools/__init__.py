# ======== from tools/eks_deployment_status_tool/ ========

"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any

from integrations.eks.eks_k8s_client import build_k8s_clients
from tools.tool_decorator import tool

logger = logging.getLogger(__name__)


def _deployment_status_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("deployment"))


def _deployment_status_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks["cluster_name"],
        "namespace": eks.get("namespace", "default"),
        "deployment_name": eks["deployment"],
        **_eks_creds(eks),
    }


@tool(
    name="get_eks_deployment_status",
    source="eks",
    description="Get EKS deployment rollout status — desired vs ready vs unavailable replicas.",
    use_cases=[
        "Checking if a deployment has unavailable replicas",
        "Verifying rollout status after a deployment change",
    ],
    requires=["cluster_name", "deployment_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "deployment_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "namespace", "deployment_name", "role_arn"],
    },
    is_available=_deployment_status_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_deployment_status_extract_params,
)
def get_eks_deployment_status(
    cluster_name: str,
    namespace: str,
    deployment_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get EKS deployment rollout status — desired vs ready vs unavailable replicas."""
    logger.info(
        "[eks] get_eks_deployment_status cluster=%s ns=%s deployment=%s",
        cluster_name,
        namespace,
        deployment_name,
    )
    try:
        _, apps_v1 = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        spec = dep.spec
        status = dep.status
        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (status.conditions or [])
        ]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "deployment_name": deployment_name,
            "desired_replicas": spec.replicas,
            "ready_replicas": status.ready_replicas,
            "available_replicas": status.available_replicas,
            "unavailable_replicas": status.unavailable_replicas,
            "conditions": conditions,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] get_eks_deployment_status FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "deployment_name": deployment_name,
            "error": str(e),
        }


# ======== from tools/eks_describe_addon_tool/ ========

"""EKS cluster-level investigation tools — boto3 backed."""


from botocore.exceptions import ClientError

from integrations.eks.eks_client import EKSClient
from tools._telemetry import report_run_error
from tools.tool_decorator import tool


def _addon_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))


def _addon_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {"cluster_name": eks["cluster_name"], "addon_name": "coredns", **_eks_creds(eks)}


@tool(
    name="describe_eks_addon",
    source="eks",
    description="Describe an EKS addon — coredns, kube-proxy, vpc-cni, aws-ebs-csi-driver, etc.",
    use_cases=[
        "Investigating DNS resolution failures (coredns)",
        "Checking networking issues (vpc-cni)",
        "Finding storage attachment failures (ebs-csi)",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "addon_name": {"type": "string", "default": "coredns"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_addon_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_addon_extract_params,
)
def describe_eks_addon(
    cluster_name: str,
    addon_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Describe an EKS addon — coredns, kube-proxy, vpc-cni, aws-ebs-csi-driver, etc."""
    try:
        client = EKSClient(
            role_arn=role_arn,
            external_id=external_id,
            region=region,
            credentials=credentials,
        )
        addon = client.describe_addon(cluster_name, addon_name)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "addon_name": addon_name,
            "status": addon.get("status"),
            "addon_version": addon.get("addonVersion"),
            "health": addon.get("health", {}),
            "marketplace_version": addon.get("marketplaceVersion"),
            "error": None,
        }
    except ClientError as e:
        report_run_error(
            e,
            tool_name="describe_eks_addon",
            source="eks",
            component="tools.eks_describe_addon_tool",
            method="EKSClient.describe_addon",
            severity="warning",
            extras={
                "cluster_name": cluster_name,
                "addon_name": addon_name,
                "region": region,
            },
        )
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "addon_name": addon_name,
            "error": str(e),
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="describe_eks_addon",
            source="eks",
            component="tools.eks_describe_addon_tool",
            method="EKSClient.describe_addon",
            extras={
                "cluster_name": cluster_name,
                "addon_name": addon_name,
                "region": region,
            },
        )
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "addon_name": addon_name,
            "error": str(e),
        }


# ======== from tools/eks_describe_cluster_tool/ ========

"""EKS cluster-level investigation tools — boto3 backed."""


from tools.tool_decorator import tool

logger = logging.getLogger(__name__)


def _describe_cluster_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))


def _describe_cluster_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}


@tool(
    name="describe_eks_cluster",
    source="eks",
    description="Describe an EKS cluster — health, version, status, endpoint, logging config.",
    use_cases=[
        "Investigating cluster-level issues: version mismatches, endpoint problems",
        "Checking if control plane logging is disabled",
        "Verifying cluster status (ACTIVE, DEGRADED, FAILED)",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_describe_cluster_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_describe_cluster_extract_params,
)
def describe_eks_cluster(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Describe an EKS cluster — health, version, status, endpoint, logging config."""
    logger.info("[eks] describe_eks_cluster cluster=%s region=%s", cluster_name, region)
    try:
        client = EKSClient(
            role_arn=role_arn,
            external_id=external_id,
            region=region,
            credentials=credentials,
        )
        cluster = client.describe_cluster(cluster_name)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "status": cluster.get("status"),
            "kubernetes_version": cluster.get("version"),
            "endpoint": cluster.get("endpoint"),
            "cluster_role_arn": cluster.get("roleArn"),
            "logging": cluster.get("logging", {}),
            "resources_vpc_config": cluster.get("resourcesVpcConfig", {}),
            "tags": cluster.get("tags", {}),
            "error": None,
        }
    except ClientError as e:
        report_run_error(
            e,
            tool_name="describe_eks_cluster",
            source="eks",
            component="tools.eks_describe_cluster_tool",
            method="EKSClient.describe_cluster",
            severity="warning",
            extras={"cluster_name": cluster_name, "region": region},
        )
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
    except Exception as e:
        report_run_error(
            e,
            tool_name="describe_eks_cluster",
            source="eks",
            component="tools.eks_describe_cluster_tool",
            method="EKSClient.describe_cluster",
            extras={"cluster_name": cluster_name, "region": region},
        )
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


# ======== from tools/eks_events_tool/ ========

"""EKS workload investigation tools — Kubernetes Python SDK backed."""


from typing import cast

from tools.tool_decorator import tool
from tools.utils.availability import eks_available_or_backend

logger = logging.getLogger(__name__)


def _events_is_available(sources: dict[str, dict]) -> bool:
    return bool(eks_available_or_backend(sources) and sources.get("eks", {}).get("cluster_name"))


def _events_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "namespace": eks.get("namespace", "default"),
        "eks_backend": eks.get("_backend"),
        **_eks_creds(eks),
    }


@tool(
    name="get_eks_events",
    source="eks",
    description="Get Kubernetes Warning events in a namespace.",
    use_cases=[
        "Finding OOMKilled, FailedScheduling, BackOff, Unhealthy, FailedMount events",
        "Understanding what Kubernetes reported during an incident",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' for all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    },
    is_available=_events_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_events_extract_params,
)
def get_eks_events(
    cluster_name: str,
    namespace: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get Kubernetes Warning events in a namespace.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] get_eks_events cluster=%s ns=%s", cluster_name, namespace)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.get_events(cluster_name=cluster_name, namespace=namespace),
        )
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        event_list = (
            core_v1.list_event_for_all_namespaces()
            if namespace == "all"
            else core_v1.list_namespaced_event(namespace=namespace)
        )
        warning_events = [
            {
                "namespace": e.metadata.namespace,
                "reason": e.reason,
                "message": e.message,
                "type": e.type,
                "count": e.count,
                "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}",
                "first_time": str(e.first_timestamp),
                "last_time": str(e.last_timestamp),
            }
            for e in event_list.items
            if e.type == "Warning"
        ]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "warning_events": warning_events,
            "total_warning_count": len(warning_events),
            "error": None,
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="get_eks_events",
            source="eks",
            component="tools.eks_events_tool",
            method="core_v1.list_namespaced_event",
            logger=logger,
            extras={"cluster_name": cluster_name, "namespace": namespace},
        )
        return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}


# ======== from tools/eks_list_clusters_tool/ ========

"""EKS cluster-level investigation tools — boto3 backed."""


from tools.tool_decorator import tool
from tools.utils.eks_workload_helper import extract_cluster_params

logger = logging.getLogger(__name__)


def _eks_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("eks", {}).get("connection_verified"))


def _eks_creds(eks: dict) -> dict:
    return {
        "role_arn": eks.get("role_arn", ""),
        "external_id": eks.get("external_id", ""),
        "region": eks.get("region", "us-east-1"),
        "credentials": eks.get("credentials"),
    }


@tool(
    name="list_eks_clusters",
    source="eks",
    description="List EKS clusters in the AWS account.",
    use_cases=[
        "Discovering what EKS clusters exist in the account",
        "Confirming a cluster name before running other EKS actions",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "cluster_names": {"type": "array", "items": {"type": "string"}},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["role_arn"],
    },
    is_available=_eks_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=extract_cluster_params,
)
def list_eks_clusters(
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    cluster_names: list | None = None,
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List EKS clusters in the AWS account."""
    logger.info("[eks] list_eks_clusters role=%s region=%s", role_arn, region)
    try:
        client = EKSClient(
            role_arn=role_arn,
            external_id=external_id,
            region=region,
            credentials=credentials,
        )
        clusters = client.list_clusters()
        if cluster_names:
            clusters = [c for c in clusters if c in cluster_names]
        return {"source": "eks", "available": True, "clusters": clusters, "error": None}
    except ClientError as e:
        report_run_error(
            e,
            tool_name="list_eks_clusters",
            source="eks",
            component="tools.eks_list_clusters_tool",
            method="EKSClient.list_clusters",
            severity="warning",
            extras={"role_arn": role_arn, "region": region},
        )
        return {"source": "eks", "available": False, "clusters": [], "error": str(e)}
    except Exception as e:
        report_run_error(
            e,
            tool_name="list_eks_clusters",
            source="eks",
            component="tools.eks_list_clusters_tool",
            method="EKSClient.list_clusters",
            extras={"role_arn": role_arn, "region": region},
        )
        return {"source": "eks", "available": False, "clusters": [], "error": str(e)}


# ======== from tools/eks_list_deployments_tool/ ========

"""EKS workload investigation tools — Kubernetes Python SDK backed."""


from tools.tool_decorator import tool
from tools.utils.eks_workload_helper import extract_workload_params

logger = logging.getLogger(__name__)


@tool(
    name="list_eks_deployments",
    source="eks",
    description="List all deployments in a namespace with replica counts and availability status.",
    use_cases=[
        "Discovering what deployments exist and which are degraded/unavailable",
        "Scanning all namespaces for degraded deployments",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' for all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    },
    is_available=eks_available_or_backend,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=extract_workload_params,
)
def list_eks_deployments(
    cluster_name: str,
    namespace: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all deployments in a namespace with replica counts and availability status.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] list_eks_deployments cluster=%s ns=%s", cluster_name, namespace)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.list_deployments(cluster_name=cluster_name, namespace=namespace),
        )
    try:
        _, apps_v1 = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        dep_list = (
            apps_v1.list_deployment_for_all_namespaces()
            if namespace == "all"
            else apps_v1.list_namespaced_deployment(namespace=namespace)
        )
        deployments = []
        for dep in dep_list.items:
            status = dep.status
            desired = dep.spec.replicas or 0
            ready = status.ready_replicas or 0
            unavailable = status.unavailable_replicas or 0
            deployments.append(
                {
                    "name": dep.metadata.name,
                    "namespace": dep.metadata.namespace,
                    "desired": desired,
                    "ready": ready,
                    "available": status.available_replicas or 0,
                    "unavailable": unavailable,
                    "degraded": unavailable > 0 or ready < desired,
                }
            )
        degraded = [d for d in deployments if d["degraded"]]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "total_deployments": len(deployments),
            "deployments": deployments,
            "degraded_deployments": degraded,
            "error": None,
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="list_eks_deployments",
            source="eks",
            component="tools.eks_list_deployments_tool",
            method="apps_v1.list_namespaced_deployment",
            logger=logger,
            extras={"cluster_name": cluster_name, "namespace": namespace},
        )
        return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}


# ======== from tools/eks_list_namespaces_tool/ ========

"""EKS workload investigation tools — Kubernetes Python SDK backed."""


from tools.tool_decorator import tool

logger = logging.getLogger(__name__)


def _list_ns_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))


def _list_ns_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}


@tool(
    name="list_eks_namespaces",
    source="eks",
    description="List all namespaces in the EKS cluster with their status.",
    use_cases=[
        "Discovering what namespaces are present before querying pods/deployments",
        "Confirming an alert namespace actually exists in the cluster",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_list_ns_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_list_ns_extract_params,
)
def list_eks_namespaces(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all namespaces in the EKS cluster with their status."""
    logger.info("[eks] list_eks_namespaces cluster=%s", cluster_name)
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        ns_list = core_v1.list_namespace()
        namespaces = [
            {
                "name": ns.metadata.name,
                "status": ns.status.phase,
                "labels": ns.metadata.labels or {},
            }
            for ns in ns_list.items
        ]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespaces": namespaces,
            "error": None,
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="list_eks_namespaces",
            source="eks",
            component="tools.eks_list_namespaces_tool",
            method="core_v1.list_namespace",
            logger=logger,
            extras={"cluster_name": cluster_name},
        )
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


# ======== from tools/eks_list_pods_tool/ ========

"""EKS workload investigation tools — Kubernetes Python SDK backed."""


from pydantic import BaseModel, Field

from tools.tool_decorator import tool
from tools.utils.availability import eks_available_or_backend
from tools.utils.eks_workload_helper import extract_workload_params

logger = logging.getLogger(__name__)


class ListEKSPodsInput(BaseModel):
    cluster_name: str = Field(description="EKS cluster name.")
    namespace: str = Field(
        description="Kubernetes namespace to inspect, or `all` for every namespace."
    )
    region: str = Field(default="us-east-1", description="AWS region of the EKS cluster.")


class ListEKSPodsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether pod listing succeeded.")
    cluster_name: str | None = Field(default=None, description="Cluster queried.")
    namespace: str = Field(description="Namespace scope used for pod query.")
    total_pods: int = Field(default=0, description="Total number of pods discovered.")
    pods: list[dict[str, Any]] = Field(
        default_factory=list, description="All pod entries returned."
    )
    failing_pods: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Pods not in Running/Succeeded phases.",
    )
    high_restart_pods: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Pods with container restart count above threshold.",
    )
    error: str | None = Field(default=None, description="Error details when listing fails.")


@tool(
    name="list_eks_pods",
    source="eks",
    description="List all pods in a namespace with their status, phase, restart counts, and conditions.",
    use_cases=[
        "Discovering what pods exist before fetching logs",
        "Finding which pods are crashing, pending, or failed",
        "Checking restart counts for crash-looping containers",
    ],
    requires=["cluster_name"],
    source_id="eks_core_v1",
    evidence_type="topology",
    side_effect_level="read_only",
    examples=[
        "List pods in `payments` namespace to identify CrashLoopBackOff pods.",
        "Use namespace `all` to detect widespread node scheduling issues.",
    ],
    anti_examples=["Use this tool to run kubectl exec or mutate Kubernetes resources."],
    input_model=ListEKSPodsInput,
    output_model=ListEKSPodsOutput,
    injected_params=("role_arn", "external_id", "credentials", "eks_backend"),
    is_available=eks_available_or_backend,
    extract_params=extract_workload_params,
)
def list_eks_pods(
    cluster_name: str,
    namespace: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all pods in a namespace with their status, phase, restart counts, and conditions.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] list_eks_pods cluster=%s ns=%s", cluster_name, namespace)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.list_pods(cluster_name=cluster_name, namespace=namespace),
        )
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        pod_list = (
            core_v1.list_pod_for_all_namespaces()
            if namespace == "all"
            else core_v1.list_namespaced_pod(namespace=namespace)
        )

        pods = []
        for pod in pod_list.items:
            containers = []
            for cs in pod.status.container_statuses or []:
                state = {}
                if cs.state.running:
                    state = {"running": True, "started_at": str(cs.state.running.started_at)}
                elif cs.state.waiting:
                    state = {
                        "waiting": True,
                        "reason": cs.state.waiting.reason,
                        "message": cs.state.waiting.message,
                    }
                elif cs.state.terminated:
                    state = {
                        "terminated": True,
                        "exit_code": cs.state.terminated.exit_code,
                        "reason": cs.state.terminated.reason,
                        "message": cs.state.terminated.message,
                    }
                containers.append(
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "state": state,
                    }
                )
            conditions = [
                {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                for c in (pod.status.conditions or [])
            ]
            pods.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node_name": pod.spec.node_name,
                    "containers": containers,
                    "conditions": conditions,
                    "start_time": str(pod.status.start_time),
                }
            )

        failing = [p for p in pods if p["phase"] not in ("Running", "Succeeded")]
        crashing = [p for p in pods if any(c["restart_count"] > 3 for c in p["containers"])]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "total_pods": len(pods),
            "pods": pods,
            "failing_pods": failing,
            "high_restart_pods": crashing,
            "error": None,
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="list_eks_pods",
            source="eks",
            component="tools.eks_list_pods_tool",
            method="core_v1.list_namespaced_pod",
            logger=logger,
            extras={"cluster_name": cluster_name, "namespace": namespace, "region": region},
        )
        return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}


# ======== from tools/eks_node_health_tool/ ========

"""EKS workload investigation tools — Kubernetes Python SDK backed."""


from tools.tool_decorator import tool
from tools.utils.availability import eks_available_or_backend

logger = logging.getLogger(__name__)


def _node_health_is_available(sources: dict[str, dict]) -> bool:
    return bool(eks_available_or_backend(sources) and sources.get("eks", {}).get("cluster_name"))


def _node_health_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "eks_backend": eks.get("_backend"),
        **_eks_creds(eks),
    }


@tool(
    name="get_eks_node_health",
    source="eks",
    description="Get health status of all EKS nodes — conditions, capacity, allocatable, pod counts.",
    use_cases=[
        "Investigating when pods are unschedulable or nodes are NotReady",
        "Checking memory/disk pressure on nodes",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_node_health_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_node_health_extract_params,
)
def get_eks_node_health(
    cluster_name: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get health status of all EKS nodes — conditions, capacity, allocatable, pod counts.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] get_eks_node_health cluster=%s", cluster_name)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.get_node_health(cluster_name=cluster_name),
        )
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        nodes = core_v1.list_node()
        node_health = []
        for node in nodes.items:
            conditions = {c.type: c.status for c in (node.status.conditions or [])}
            capacity = node.status.capacity or {}
            allocatable = node.status.allocatable or {}
            addresses = {a.type: a.address for a in (node.status.addresses or [])}
            node_health.append(
                {
                    "name": node.metadata.name,
                    "internal_ip": addresses.get("InternalIP"),
                    "ready": conditions.get("Ready"),
                    "memory_pressure": conditions.get("MemoryPressure"),
                    "disk_pressure": conditions.get("DiskPressure"),
                    "pid_pressure": conditions.get("PIDPressure"),
                    "capacity_cpu": capacity.get("cpu"),
                    "capacity_memory": capacity.get("memory"),
                    "allocatable_cpu": allocatable.get("cpu"),
                    "allocatable_memory": allocatable.get("memory"),
                    "instance_type": node.metadata.labels.get("node.kubernetes.io/instance-type")
                    if node.metadata.labels
                    else None,
                }
            )
        not_ready = sum(1 for n in node_health if n["ready"] != "True")
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "nodes": node_health,
            "total_nodes": len(node_health),
            "not_ready_count": not_ready,
            "error": None,
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="get_eks_node_health",
            source="eks",
            component="tools.eks_node_health_tool",
            method="core_v1.list_node",
            logger=logger,
            extras={"cluster_name": cluster_name, "region": region},
        )
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


# ======== from tools/eks_nodegroup_health_tool/ ========

"""EKS cluster-level investigation tools — boto3 backed."""


from tools.tool_decorator import tool


def _nodegroup_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))


def _nodegroup_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}


@tool(
    name="get_eks_nodegroup_health",
    source="eks",
    description="Get EKS node group health — instance types, scaling config, AMI version, health issues.",
    use_cases=[
        "Investigating when pods are unschedulable or nodes are NotReady",
        "Checking node capacity and scaling configuration",
        "Finding AMI version issues in EKS node groups",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "nodegroup_name": {"type": "string"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_nodegroup_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_nodegroup_extract_params,
)
def get_eks_nodegroup_health(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    nodegroup_name: str | None = None,
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get EKS node group health — instance types, scaling config, AMI version, health issues."""
    # Track which nodegroup is being processed so a mid-loop failure can be
    # tagged with the actual failing name rather than the (possibly None)
    # caller-supplied input — matches the per-resource extras used by the
    # other migrated EKS tools (e.g. ``addon_name``, ``pod_name``).
    current_nodegroup: str | None = nodegroup_name
    try:
        client = EKSClient(
            role_arn=role_arn,
            external_id=external_id,
            region=region,
            credentials=credentials,
        )
        nodegroups = [nodegroup_name] if nodegroup_name else client.list_nodegroups(cluster_name)
        results = []
        for ng in nodegroups:
            current_nodegroup = ng
            ng_data = client.describe_nodegroup(cluster_name, ng)
            results.append(
                {
                    "name": ng,
                    "status": ng_data.get("status"),
                    "instance_types": ng_data.get("instanceTypes", []),
                    "scaling_config": ng_data.get("scalingConfig", {}),
                    "release_version": ng_data.get("releaseVersion"),
                    "health": ng_data.get("health", {}),
                    "node_role": ng_data.get("nodeRole"),
                    "labels": ng_data.get("labels", {}),
                    "taints": ng_data.get("taints", []),
                }
            )
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "nodegroups": results,
            "error": None,
        }
    except ClientError as e:
        report_run_error(
            e,
            tool_name="get_eks_nodegroup_health",
            source="eks",
            component="tools.eks_nodegroup_health_tool",
            method="EKSClient.describe_nodegroup",
            severity="warning",
            extras={
                "cluster_name": cluster_name,
                "region": region,
                "nodegroup_name": current_nodegroup,
            },
        )
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
    except Exception as e:
        report_run_error(
            e,
            tool_name="get_eks_nodegroup_health",
            source="eks",
            component="tools.eks_nodegroup_health_tool",
            method="EKSClient.describe_nodegroup",
            extras={
                "cluster_name": cluster_name,
                "region": region,
                "nodegroup_name": current_nodegroup,
            },
        )
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


# ======== from tools/eks_pod_logs_tool/ ========

"""EKS workload investigation tools — Kubernetes Python SDK backed."""


from tools.tool_decorator import tool
from tools.utils.availability import eks_available_or_backend

logger = logging.getLogger(__name__)


def _pod_logs_is_available(sources: dict[str, dict]) -> bool:
    return bool(eks_available_or_backend(sources) and sources.get("eks", {}).get("pod_name"))


def _pod_logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "namespace": eks.get("namespace", "default"),
        "pod_name": eks.get("pod_name", ""),
        "eks_backend": eks.get("_backend"),
        **_eks_creds(eks),
    }


@tool(
    name="get_eks_pod_logs",
    source="eks",
    description="Fetch logs from a specific EKS pod.",
    use_cases=[
        "Fetching crash logs from a specific pod",
        "Reviewing application output for a known failing pod",
    ],
    requires=["cluster_name", "pod_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "pod_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
            "tail_lines": {"type": "integer", "default": 100},
        },
        "required": ["cluster_name", "namespace", "pod_name", "role_arn"],
    },
    is_available=_pod_logs_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_pod_logs_extract_params,
)
def get_eks_pod_logs(
    cluster_name: str,
    namespace: str,
    pod_name: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    tail_lines: int = 100,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch logs from a specific EKS pod.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] get_eks_pod_logs cluster=%s ns=%s pod=%s", cluster_name, namespace, pod_name)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.get_pod_logs(
                cluster_name=cluster_name, namespace=namespace, pod_name=pod_name
            ),
        )
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        logs = core_v1.read_namespaced_pod_log(
            name=pod_name, namespace=namespace, tail_lines=tail_lines
        )
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "pod_name": pod_name,
            "logs": logs,
            "error": None,
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="get_eks_pod_logs",
            source="eks",
            component="tools.eks_pod_logs_tool",
            method="core_v1.read_namespaced_pod_log",
            logger=logger,
            extras={
                "cluster_name": cluster_name,
                "namespace": namespace,
                "pod_name": pod_name,
            },
        )
        return {"source": "eks", "available": False, "pod_name": pod_name, "error": str(e)}

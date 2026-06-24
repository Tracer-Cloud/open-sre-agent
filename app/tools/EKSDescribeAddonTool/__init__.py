"""EKS cluster-level investigation tools — boto3 backed."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from app.services.eks.eks_client import EKSClient
from app.tools._telemetry import report_run_error
from app.tools.EKSListClustersTool import _eks_available, _eks_creds
from app.tools.tool_decorator import tool


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
            component="app.tools.EKSDescribeAddonTool",
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
            component="app.tools.EKSDescribeAddonTool",
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

"""VPC lookup and security group management for gateway deployment."""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from infra.deploy_gateway.aws_client import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)

logger = logging.getLogger(__name__)


def get_default_vpc(region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Get default VPC."""
    ec2_client = get_boto3_client("ec2", region)
    response = ec2_client.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])

    if not response["Vpcs"]:
        raise ValueError(f"No default VPC found in region {region}")

    vpc = response["Vpcs"][0]
    return {
        "vpc_id": vpc["VpcId"],
        "cidr": vpc["CidrBlock"],
    }


def get_public_subnets(vpc_id: str, region: str = DEFAULT_REGION) -> list[str]:
    """Get public subnet IDs in VPC."""
    ec2_client = get_boto3_client("ec2", region)
    response = ec2_client.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])

    subnet_ids = []
    for subnet in response["Subnets"]:
        if subnet.get("MapPublicIpOnLaunch", False) or _has_internet_gateway_route(
            subnet["SubnetId"], ec2_client
        ):
            subnet_ids.append(subnet["SubnetId"])

    if not subnet_ids:
        subnet_ids = [s["SubnetId"] for s in response["Subnets"]]

    return subnet_ids


def _has_internet_gateway_route(subnet_id: str, ec2_client: Any) -> bool:
    """Check if subnet has route to internet gateway."""
    response = ec2_client.describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
    )

    if not response["RouteTables"]:
        response = ec2_client.describe_route_tables(
            Filters=[{"Name": "association.main", "Values": ["true"]}]
        )

    for rt in response.get("RouteTables", []):
        for route in rt.get("Routes", []):
            if route.get("GatewayId", "").startswith("igw-"):
                return True

    return False


def create_security_group(
    name: str,
    vpc_id: str,
    description: str,
    ingress_rules: list[dict[str, Any]] | None = None,
    stack_name: str | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create security group."""
    ec2_client = get_boto3_client("ec2", region)

    try:
        response = ec2_client.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )
        if response["SecurityGroups"]:
            sg = response["SecurityGroups"][0]
            return {
                "group_id": sg["GroupId"],
                "arn": f"arn:aws:ec2:{region}:{sg['OwnerId']}:security-group/{sg['GroupId']}",
            }
    except ClientError:
        logger.debug("Security group lookup failed before create", exc_info=True)

    tag_specs = []
    if stack_name:
        tag_specs = [
            {
                "ResourceType": "security-group",
                "Tags": get_standard_tags(stack_name) + [{"Key": "Name", "Value": name}],
            }
        ]

    response = ec2_client.create_security_group(
        GroupName=name,
        Description=description,
        VpcId=vpc_id,
        TagSpecifications=tag_specs if tag_specs else None,
    )

    group_id = response["GroupId"]

    if ingress_rules:
        for rule in ingress_rules:
            _add_ingress_rule(ec2_client, group_id, rule)

    sg_response = ec2_client.describe_security_groups(GroupIds=[group_id])
    owner_id = sg_response["SecurityGroups"][0]["OwnerId"]

    return {
        "group_id": group_id,
        "arn": f"arn:aws:ec2:{region}:{owner_id}:security-group/{group_id}",
    }


def _add_ingress_rule(ec2_client: Any, group_id: str, rule: dict[str, Any]) -> None:
    """Add an ingress rule to security group."""
    port = rule.get("port")
    cidr = rule.get("cidr", "0.0.0.0/0")
    protocol = rule.get("protocol", "tcp")
    from_port = rule.get("from_port", port)
    to_port = rule.get("to_port", port)
    description = rule.get("description", f"Allow port {port}")

    ip_permission: dict[str, Any] = {
        "IpProtocol": protocol,
        "FromPort": from_port,
        "ToPort": to_port,
    }

    if cidr:
        ip_permission["IpRanges"] = [{"CidrIp": cidr, "Description": description}]

    if rule.get("source_security_group"):
        ip_permission["UserIdGroupPairs"] = [{"GroupId": rule["source_security_group"]}]

    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[ip_permission],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise


def delete_security_group(group_id: str, region: str = DEFAULT_REGION) -> None:
    """Delete security group."""
    ec2_client = get_boto3_client("ec2", region)

    try:
        ec2_client.delete_security_group(GroupId=group_id)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ["InvalidGroup.NotFound"]:
            raise

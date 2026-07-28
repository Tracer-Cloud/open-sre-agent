"""Synth-level tests for the shared Fargate fleet CDK stack."""

from __future__ import annotations

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App
from aws_cdk.assertions import Match, Template

from platform.deployment_fargate.fargate_fleet_infrastructure.fargate_fleet_stack import (
    FargateFleetStack,
)

_TENANT_FLEET_ENV_OUTPUTS = (
    "OpensreFargateClusterArn",
    "OpensreEcsExecutionRoleArn",
    "OpensreGatewayLogGroup",
    "OpensreFargateSubnetIds",
    "OpensreFargateSecurityGroupIds",
    "OpensreS3FilesMountSecurityGroupIds",
    "OpensreS3FilesystemId",
    "OpensreS3FilesystemArn",
    "OpensreGatewayImage",
    "OpensreCredentialsApiUrl",
    "OpensreFargateResourcePrefix",
)

_REQUIRED_PARAMETERS = (
    "VpcId",
    "PublicSubnetIds",
    "S3FileSystemId",
    "S3FileSystemArn",
    "S3FilesClientSecurityGroupId",
    "GatewayImage",
)


@pytest.fixture
def fleet_template() -> Template:
    app = App()
    stack = FargateFleetStack(app, "TestFleet")
    return Template.from_stack(stack)


def test_creates_ecs_cluster_with_container_insights(fleet_template: Template) -> None:
    fleet_template.has_resource_properties(
        "AWS::ECS::Cluster",
        {
            "ClusterSettings": Match.array_with(
                [{"Name": "containerInsights", "Value": "enabled"}],
            ),
        },
    )


def test_creates_fargate_capacity_providers(fleet_template: Template) -> None:
    fleet_template.resource_count_is("AWS::ECS::ClusterCapacityProviderAssociations", 1)


def test_gateway_security_group_is_outbound_only(fleet_template: Template) -> None:
    fleet_template.has_resource_properties(
        "AWS::EC2::SecurityGroup",
        {
            "GroupDescription": "Tenant Gateway Fargate tasks - outbound only",
            "SecurityGroupIngress": Match.absent(),
        },
    )


def test_mount_target_security_group_accepts_only_gateway_nfs(
    fleet_template: Template,
) -> None:
    fleet_template.has_resource_properties(
        "AWS::EC2::SecurityGroup",
        {
            "GroupDescription": "S3 Files mount targets - NFS from Gateway tasks only",
        },
    )
    fleet_template.has_resource_properties(
        "AWS::EC2::SecurityGroupIngress",
        {
            "IpProtocol": "tcp",
            "FromPort": 2049,
            "ToPort": 2049,
            "SourceSecurityGroupId": Match.any_value(),
        },
    )


def test_gateway_log_group_is_retained_with_retention(fleet_template: Template) -> None:
    fleet_template.has_resource_properties(
        "AWS::Logs::LogGroup",
        {
            "RetentionInDays": 30,
        },
    )
    fleet_template.has_resource(
        "AWS::Logs::LogGroup",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )


def test_execution_role_uses_ecs_task_execution_managed_policy(
    fleet_template: Template,
) -> None:
    fleet_template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                                },
                            ),
                        ],
                    ),
                },
            ),
        },
    )
    roles = fleet_template.find_resources("AWS::IAM::Role")
    execution_roles = [
        props
        for props in roles.values()
        if props.get("Properties", {})
        .get("Description", "")
        .startswith(
            "ECS execution role for tenant Gateway tasks",
        )
    ]
    assert len(execution_roles) == 1
    managed = execution_roles[0]["Properties"].get("ManagedPolicyArns", [])
    assert "AmazonECSTaskExecutionRolePolicy" in str(managed)


def test_requires_explicit_network_and_existing_resource_parameters(
    fleet_template: Template,
) -> None:
    for parameter_name in _REQUIRED_PARAMETERS:
        fleet_template.has_parameter(parameter_name, {})


def test_gateway_image_parameter_requires_digest_pinned_ecr_uri(
    fleet_template: Template,
) -> None:
    fleet_template.has_parameter(
        "GatewayImage",
        {
            "AllowedPattern": Match.string_like_regexp("sha256"),
        },
    )


def test_s3_files_client_security_group_parameter_is_security_group_id(
    fleet_template: Template,
) -> None:
    fleet_template.has_parameter(
        "S3FilesClientSecurityGroupId",
        {
            "Type": "AWS::EC2::SecurityGroup::Id",
        },
    )


def test_task_security_group_output_includes_external_client_sg(
    fleet_template: Template,
) -> None:
    outputs = fleet_template.find_outputs("*")
    security_groups = outputs["OpensreFargateSecurityGroupIds"]["Value"]
    assert isinstance(security_groups, dict)
    assert security_groups["Fn::Join"][0] == ","
    joined = security_groups["Fn::Join"][1]
    assert any(
        isinstance(part, dict) and "Ref" in part and "S3FilesClientSecurityGroupId" in part["Ref"]
        for part in joined
    )
    assert any(isinstance(part, dict) and "Fn::GetAtt" in part for part in joined)


def test_credentials_api_url_export_falls_back_to_sentinel(
    fleet_template: Template,
) -> None:
    """Empty CredentialsApiUrl must export the non-empty sentinel, not ""."""
    outputs = fleet_template.find_outputs("*")
    value = outputs["OpensreCredentialsApiUrl"]["Value"]
    assert isinstance(value, dict) and "Fn::If" in value
    condition_name, when_set, when_unset = value["Fn::If"]
    assert "CredentialsApiUrlProvided" in condition_name
    assert when_set == {"Ref": "CredentialsApiUrl"}
    assert when_unset == "-"


def test_emits_tenant_fleet_config_outputs(fleet_template: Template) -> None:
    outputs = fleet_template.find_outputs("*")
    for logical_id in _TENANT_FLEET_ENV_OUTPUTS:
        assert logical_id in outputs, f"Missing output {logical_id}"


def test_output_exports_use_opensre_fleet_prefix(fleet_template: Template) -> None:
    outputs = fleet_template.find_outputs("*")
    for logical_id in _TENANT_FLEET_ENV_OUTPUTS:
        export_name = outputs[logical_id].get("Export", {}).get("Name")
        assert export_name is not None
        assert export_name.startswith("opensre-fleet:OPENSRE-")
        assert "_" not in export_name


def test_stack_synths_without_aws_credentials() -> None:
    app = App()
    FargateFleetStack(app, "CiFleet")
    assembly = app.synth()
    assert assembly.stacks
    assert any(item.stack_name == "CiFleet" for item in assembly.stacks)

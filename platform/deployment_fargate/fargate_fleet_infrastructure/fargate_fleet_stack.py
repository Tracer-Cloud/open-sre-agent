"""Shared ECS Fargate fleet foundation for tenant Gateway services."""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnCondition, CfnOutput, CfnParameter, Fn, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from platform.deployment_fargate.infrastructure_exports import (
    FLEET_EXPORT_UNSET,
    fleet_export_name,
)


class FargateFleetStack(Stack):
    """Provision stable shared resources consumed by ``TenantFleetConfig``.

    Per-organization task roles, secrets, task definitions, and ECS services
    remain owned by the Python control-plane lifecycle.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc_id = CfnParameter(
            self,
            "VpcId",
            type="AWS::EC2::VPC::Id",
            description="Existing VPC where tenant Gateway Fargate tasks run",
        )
        public_subnet_ids = CfnParameter(
            self,
            "PublicSubnetIds",
            type="CommaDelimitedList",
            description=(
                "Explicit public subnet IDs for Gateway tasks; the MVP assigns "
                "public IPs and does not provision NAT"
            ),
        )
        s3_file_system_id = CfnParameter(
            self,
            "S3FileSystemId",
            type="String",
            description="Existing S3 Files filesystem ID shared by tenant Gateways",
        )
        s3_file_system_arn = CfnParameter(
            self,
            "S3FileSystemArn",
            type="String",
            description="Existing S3 Files filesystem ARN shared by tenant Gateways",
        )
        s3_files_client_security_group_id = CfnParameter(
            self,
            "S3FilesClientSecurityGroupId",
            type="AWS::EC2::SecurityGroup::Id",
            description=(
                "Existing S3 Files client security group from opensre-infra-aws; "
                "attached to Gateway tasks so mount targets accept NFS"
            ),
        )
        gateway_image = CfnParameter(
            self,
            "GatewayImage",
            type="String",
            allowed_pattern=(
                r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/"
                r"[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}"
            ),
            constraint_description="Must be an immutable ECR image URI pinned by sha256",
            description="Immutable ECR gateway image URI pinned by sha256 digest",
        )
        credentials_api_url = CfnParameter(
            self,
            "CredentialsApiUrl",
            type="String",
            default="",
            allowed_pattern=r"^$|^https://[^ ]+$",
            constraint_description="Must be empty or an HTTPS URL",
            description="Credentials API base URL for Gateway bootstrap (non-secret)",
        )
        resource_prefix = CfnParameter(
            self,
            "ResourcePrefix",
            type="String",
            default="opensre",
            allowed_pattern=r"[a-z][a-z0-9-]{1,31}",
            constraint_description=(
                "Must be 2-32 lowercase letters, digits, or hyphens, starting with a letter"
            ),
            description="Prefix for shared fleet resource names",
        )

        vpc = ec2.Vpc.from_vpc_attributes(
            self,
            "ImportedVpc",
            vpc_id=vpc_id.value_as_string,
            availability_zones=Fn.get_azs(Fn.ref("AWS::Region")),
        )

        cluster = ecs.Cluster(
            self,
            "GatewayCluster",
            cluster_name=Fn.join("", [resource_prefix.value_as_string, "-gateways"]),
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )
        cluster.enable_fargate_capacity_providers()

        gateway_security_group = ec2.SecurityGroup(
            self,
            "GatewayTaskSecurityGroup",
            vpc=vpc,
            description="Tenant Gateway Fargate tasks - outbound only",
            allow_all_outbound=True,
            security_group_name=Fn.join(
                "",
                [resource_prefix.value_as_string, "-gateway-tasks"],
            ),
        )
        mount_security_group = ec2.SecurityGroup(
            self,
            "S3FilesMountTargetSecurityGroup",
            vpc=vpc,
            description="S3 Files mount targets - NFS from Gateway tasks only",
            allow_all_outbound=False,
            security_group_name=Fn.join(
                "",
                [resource_prefix.value_as_string, "-s3files-mount-targets"],
            ),
        )
        mount_security_group.add_ingress_rule(
            gateway_security_group,
            ec2.Port.tcp(2049),
            "Allow S3 Files mounts from Gateway tasks",
        )

        log_group = logs.LogGroup(
            self,
            "GatewayLogGroup",
            log_group_name=Fn.join("", ["/", resource_prefix.value_as_string, "/gateways"]),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )

        execution_role = iam.Role(
            self,
            "GatewayExecutionRole",
            role_name=Fn.join("", [resource_prefix.value_as_string, "-gateway-execution"]),
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="ECS execution role for tenant Gateway tasks",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy",
                ),
            ],
        )

        self._emit_fleet_outputs(
            execution_role=execution_role,
            gateway_security_group=gateway_security_group,
            mount_security_group=mount_security_group,
            log_group=log_group,
            cluster_arn=cluster.cluster_arn,
            public_subnet_ids=public_subnet_ids,
            s3_file_system_id=s3_file_system_id,
            s3_file_system_arn=s3_file_system_arn,
            s3_files_client_security_group_id=s3_files_client_security_group_id,
            gateway_image=gateway_image,
            credentials_api_url=credentials_api_url,
            resource_prefix=resource_prefix,
        )

    def _emit_fleet_outputs(
        self,
        *,
        cluster_arn: str,
        execution_role: iam.Role,
        gateway_security_group: ec2.SecurityGroup,
        mount_security_group: ec2.SecurityGroup,
        log_group: logs.LogGroup,
        public_subnet_ids: CfnParameter,
        s3_file_system_id: CfnParameter,
        s3_file_system_arn: CfnParameter,
        s3_files_client_security_group_id: CfnParameter,
        gateway_image: CfnParameter,
        credentials_api_url: CfnParameter,
        resource_prefix: CfnParameter,
    ) -> None:
        """Emit outputs aligned with ``TenantFleetConfig`` environment variables."""
        gateway_task_security_group_ids = Fn.join(
            ",",
            [
                gateway_security_group.security_group_id,
                s3_files_client_security_group_id.value_as_string,
            ],
        )
        # Exports must not be empty; substitute the sentinel when the optional
        # CredentialsApiUrl parameter is blank (readers normalize it back).
        credentials_api_url_provided = CfnCondition(
            self,
            "CredentialsApiUrlProvided",
            expression=Fn.condition_not(
                Fn.condition_equals(credentials_api_url.value_as_string, ""),
            ),
        )
        credentials_api_url_export = Fn.condition_if(
            credentials_api_url_provided.logical_id,
            credentials_api_url.value_as_string,
            FLEET_EXPORT_UNSET,
        ).to_string()
        outputs: tuple[tuple[str, Any, str], ...] = (
            ("OpensreFargateClusterArn", cluster_arn, "OPENSRE_FARGATE_CLUSTER_ARN"),
            (
                "OpensreEcsExecutionRoleArn",
                execution_role.role_arn,
                "OPENSRE_ECS_EXECUTION_ROLE_ARN",
            ),
            ("OpensreGatewayLogGroup", log_group.log_group_name, "OPENSRE_GATEWAY_LOG_GROUP"),
            (
                "OpensreFargateSubnetIds",
                Fn.join(",", public_subnet_ids.value_as_list),
                "OPENSRE_FARGATE_SUBNET_IDS",
            ),
            (
                "OpensreFargateSecurityGroupIds",
                gateway_task_security_group_ids,
                "OPENSRE_FARGATE_SECURITY_GROUP_IDS",
            ),
            (
                "OpensreS3FilesMountSecurityGroupIds",
                mount_security_group.security_group_id,
                "OPENSRE_S3_FILES_MOUNT_SECURITY_GROUP_IDS",
            ),
            (
                "OpensreS3FilesystemId",
                s3_file_system_id.value_as_string,
                "OPENSRE_S3_FILESYSTEM_ID",
            ),
            (
                "OpensreS3FilesystemArn",
                s3_file_system_arn.value_as_string,
                "OPENSRE_S3_FILESYSTEM_ARN",
            ),
            ("OpensreGatewayImage", gateway_image.value_as_string, "OPENSRE_GATEWAY_IMAGE"),
            (
                "OpensreCredentialsApiUrl",
                credentials_api_url_export,
                "OPENSRE_CREDENTIALS_API_URL",
            ),
            (
                "OpensreFargateResourcePrefix",
                resource_prefix.value_as_string,
                "OPENSRE_FARGATE_RESOURCE_PREFIX",
            ),
        )
        for logical_id, value, env_name in outputs:
            CfnOutput(
                self,
                logical_id,
                value=value,
                description=f"Maps to control-plane env var {env_name}",
                export_name=fleet_export_name(env_name),
            )
